import asyncio
import builtins
from asyncio import Event, Future, Lock, Task
from dataclasses import dataclass
from typing import Any, Optional, overload

import serial.tools.list_ports
from serial import Serial
from serial.serialutil import SerialException

from .util import aexit_handler


Datatype = type[bool] | type[int]

class AMFDeviceConnectionError(Exception):
  pass


@dataclass(frozen=True, kw_only=True)
class AMFDeviceInfo:
  address: str

  def create(self):
    return AMFDevice(self.address)


class AMFDevice:
  def __init__(self, address: str):
    """
    Constructs an `AMFDevice` instance and opens the connection to the device.

    Parameters
      address: The address of the device, such as `COM3` or `/dev/tty.usbmodem1101`.

    Raises
      AMFDeviceConnectionError: If the device is unreachable.
    """

    self.address = address

    try:
      self._serial = Serial(
        baudrate=9600,
        port=address
      )
    except (OSError, SerialException) as e:
      raise AMFDeviceConnectionError from e

    self._busy = False
    self._closing = False
    self._error_event = Event()
    self._query_future = Future[Any]()
    self._query_lock = Lock()
    self._read_task: Optional[Task[None]] = None
    self._run_future: Optional[Future[Any]] = None
    self._run_lock = Lock()

  async def _read_loop(self):
    try:
      while True:
        # Call _receive() to process the received data
        self._receive((await asyncio.to_thread(lambda: self._serial.read_until(b"\n")))[0:-2])
    except SerialException as e:
      raise AMFDeviceConnectionError from e
    finally:
      if not self._closing:
        self._error_event.set()

      # Raise exceptions in all the current futures

      if self._query_future and not self._query_future.done():
        self._query_future.set_exception(AMFDeviceConnectionError())
      if self._run_future and not self._run_future.done():
        self._run_future.set_exception(AMFDeviceConnectionError())

  @overload
  async def _query(self, command: str, dtype: type[bool]) -> bool:
    pass

  @overload
  async def _query(self, command: str, dtype: type[int]) -> int:
    pass

  @overload
  async def _query(self, command: str, dtype = None) -> str:
    pass

  async def _query(self, command: str, dtype: Optional[Datatype] = None):
    async with self._query_lock:
      if self._closing or self._error_event.is_set():
        raise AMFDeviceConnectionError

      self._query_future = Future[Any]()

      try:
        await asyncio.to_thread(lambda: self._serial.write(f"/_{command}\r".encode()))
        return self._parse(await asyncio.wait_for(asyncio.shield(self._query_future), timeout=2.0), dtype=dtype)
      except (SerialException, asyncio.TimeoutError) as e:
        self._error_event.set()
        raise AMFDeviceConnectionError from e
      finally:
        self._query_future = None

  def _parse(self, data: bytes, dtype: Optional[Datatype] = None):
    response = data[3:-1].decode()

    match dtype:
      case builtins.bool:
        return (response == "1")
      case builtins.int:
        return int(response)
      case _:
        return response

  def _receive(self, data: bytes):
    was_busy = self._busy
    self._busy = (data[2] & (1 << 5)) < 1

    if self._run_future and was_busy and (not self._busy):
      self._run_future.set_result(data)
    elif self._query_future:
      self._query_future.set_result(data)
    else:
      raise Exception("Dropping data")

  async def _run(self, command: str):
    async with self._run_lock:
      self._run_future = Future[Any]()

      try:
        await self._query(command)
        await asyncio.shield(self._run_future)
      finally:
        self._run_future = None

  async def get_unique_id(self):
    """
    Returns the unique id of this device.

    Returns
      The unique id of this device, such as `...`.
    """

    return await self._query("?9000")

  async def get_valve(self):
    """
    Returns the current valve position.

    Returns
      The current valve position, starting at 1, or `None` if the device has not been initialized.
    """

    res = await self._query("?6", dtype=int)
    return res if res != 0 else None

  async def get_valve_count(self):
    """
    Returns the number of valves available on the rotary valve.
    """

    return await self._query("?801", dtype=int)

  async def home(self):
    """
    Initializes the rotary valve.

    The rotary valve must be initialized every time it is powered on. If it has already been initialized when calling this function, it will be re-initialized. The initialization status can be obtained by calling `get_valve()`, which returns `None` if the device has not yet been initialized.
    """

    await self._run("ZR")

  async def rotate(self, valve: int, /):
    """
    Rotates the rotary valve to a new valve position.

    A 360º rotation occurs if `valve` is already the current position. To avoid this behavior, check the current position with `get_valve()`.

    Parameters
      valve: The valve position to rotate to, starting at 1.
    """

    await self._run(f"b{valve}R")

  async def wait(self, delay: float, /):
    """
    Instructs the rotary valve to wait for a fixed time duration.

    Parameters
      delay: The delay, in seconds. The resolution is 1 ms.
    """

    await self._run(f"M{round(delay * 1000)}R")

  async def wait_error(self):
    await self._error_event.wait()

  async def __aenter__(self):
    await self.open()
    return self

  @aexit_handler
  async def __aexit__(self, failed: bool):
    if not failed:
      async with (self._query_lock, self._run_lock):
        await self.close()
    else:
      await self.close()

  async def open(self):
    if self._read_task:
      raise AMFDeviceConnectionError

    self._read_task = asyncio.create_task(self._read_loop())

    try:
      # Set answer mode to asynchronous
      await self._query("!501")
    except Exception:
      # Avoid preventing __aexit__() from being called
      await self.close()
      raise

  async def close(self):
    """
    Closes the device.

    Raises
      AMFDeviceConnectionLostError: If the device was already closed.
    """

    if self._closing or (not self._read_task):
      raise AMFDeviceConnectionError

    self._closing = True

    # Cancel the read task, if any

    self._read_task.cancel()

    try:
      await self._read_task
    except asyncio.CancelledError:
      pass
    finally:
      del self._read_task

    self._serial.close()

  @staticmethod
  def list():
    """
    Lists visible devices.

    Yields
      Instances of `AMFDeviceInfo`.
    """

    for item in serial.tools.list_ports.comports():
      yield AMFDeviceInfo(address=item.device)


__all__ = [
  'AMFDevice',
  'AMFDeviceConnectionError',
  'AMFDeviceInfo'
]
