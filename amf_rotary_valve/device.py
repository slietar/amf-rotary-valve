import asyncio
from asyncio import Future
import builtins
from dataclasses import dataclass
from typing import Optional, overload
from serial import Serial

import serial.tools.list_ports
from serial.serialutil import SerialException


Datatype = type[bool] | type[int]

class AMFDeviceConnectionError(Exception):
  pass

class AMFDeviceConnectionLostError(AMFDeviceConnectionError):
  pass


@dataclass(frozen=True, kw_only=True)
class AMFDeviceInfo:
  address: str

  def create(self):
    return AMFDevice(self.address)


class AMFDevice:
  def __init__(self, address: str):
    self.address = address

    self._closed = Future[bool]()

    try:
      self._serial: Optional[Serial] = Serial(
        baudrate=9600,
        port=address
      )
    except (OSError, SerialException) as e:
      raise AMFDeviceConnectionError from e

    self._busy = False
    self._busy_future: Optional[Future] = None
    self._closed_exc = Future[None]()
    self._closing = False
    self._query_futures = list[Future]()
    self._main_task = asyncio.create_task(self._main_func())

  async def _main_func(self):
    read_task = asyncio.create_task(self._read_loop())

    try:
      await asyncio.shield(asyncio.wait([
        self._closed_exc,
        read_task
      ], return_when=asyncio.FIRST_COMPLETED))
    except asyncio.CancelledError: # Called .close()
      pass

    if self._busy_future:
      self._busy_future.set_exception(AMFDeviceConnectionError())

    for future in self._query_futures:
      future.set_exception(AMFDeviceConnectionError())

    assert self._serial
    self._serial.close()
    self._serial = None

    if not self._closing:
      read_task.cancel()
      self._closing = True

    try:
      await read_task
    except asyncio.CancelledError:
      pass
    except SerialException as e:
      raise AMFDeviceConnectionLostError from e

    if self._closed_exc.done() and (exc := self._closed_exc.exception()):
      raise AMFDeviceConnectionLostError from exc

  async def _read_loop(self):
    loop = asyncio.get_event_loop()

    try:
      while True:
        assert self._serial
        serial = self._serial

        self._receive((await loop.run_in_executor(None, lambda: serial.read_until(b"\n")))[0:-2])
    finally:
      self._read_task = None

  async def close(self):
    """
    Closes the device.
    """

    if not self._closing:
      self._closing = True
      futures = self._query_futures + ([self._busy_future] if self._busy_future else list())

      if futures:
        await asyncio.wait(futures)

      self._main_task.cancel()

    try:
      await self._main_task
    except AMFDeviceConnectionLostError:
      pass
    except asyncio.CancelledError: # If the the main task didn't have time to start.
      pass

  async def closed(self):
    """
    Waits for the device to close.

    When cancelled, closes the device and waits for it to close.

    Raises
      asyncio.CancelledError
      AMFDeviceConnectionLostError: If the connection was lost rather than closed explicitly.
    """

    try:
      await asyncio.shield(self._main_task)
    except asyncio.CancelledError:
      await self.close()
      raise

  @overload
  async def _query(self, command: str, dtype: type[bool]) -> bool:
    pass

  @overload
  async def _query(self, command: str, dtype: type[int]) -> int:
    pass

  @overload
  async def _query(self, command: str, dtype = None) -> str:
    pass

  async def _query(self, command: str, dtype: Optional[type[bool] | type[int]] = None):
    if self._closing:
      raise AMFDeviceConnectionError

    future = Future()
    self._query_futures.append(future)

    loop = asyncio.get_event_loop()

    try:
      try:
        assert (serial := self._serial)
        await loop.run_in_executor(None, lambda: serial.write(f"/_{command}\r".encode("utf-8")))
        return self._parse(await asyncio.wait_for(future, timeout=2.0), dtype=dtype)
      except:
        self._query_futures.remove(future)
        raise
    except (SerialException, asyncio.TimeoutError) as e:
      self._closed_exc.set_exception(e)
      await self.closed()

      # This statement won't is unreachable as self.closed() will raise an AMFDeviceConnectionLostError.
      raise

  def _parse(self, data: bytes, dtype: Optional[Datatype] = None):
    response = data[3:-1].decode("utf-8")

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

    if self._busy_future and was_busy and (not self._busy):
      self._busy_future.set_result(data)
      self._busy_future = None
    else:
      query_future, *self._query_futures = self._query_futures
      query_future.set_result(data)

  async def _run(self, command: str):
    if self._closing:
      raise AMFDeviceConnectionError

    while self._busy_future:
      await self._busy_future

    future = Future()
    self._busy_future = future

    try:
      await self._query(command)
      await future
    finally:
      self._busy_future = None

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

  async def __aenter__(self):
    assert not self._closing

  async def __aexit__(self, exc_type, exc, tb):
    await self.close()

  @staticmethod
  def list(*, all: bool = False):
    """
    Lists visible devices.

    Parameters
      all: Whether to include devices that do not have recognized vendor and product ids.

    Yields
      Instances of `AMFDeviceInfo`.
    """

    for item in serial.tools.list_ports.comports():
      # if all or (item.vid, item.pid) == (0x03eb, 0x2404):
      if all:
        yield AMFDeviceInfo(address=item.device)


__all__ = [
  'AMFDevice',
  'AMFDeviceConnectionError',
  'AMFDeviceConnectionLostError',
  'AMFDeviceInfo'
]
