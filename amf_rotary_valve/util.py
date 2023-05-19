from typing import Any, Awaitable, Callable


def aexit_handler(func: Callable[[Any, bool], Awaitable[None]], /):
  async def new_func(self, exc_type, exc_value, traceback):
    exceptions = list[BaseException]()

    if exc_type:
      exceptions.append(exc_value)

    try:
      await func(self, (exc_type is not None))
    except BaseException as e:
      exceptions.append(e)

    if len(exceptions) > 1:
      raise BaseExceptionGroup("Asynchronous exit handler", exceptions) from None
    elif exceptions:
      raise exceptions[0] from None

  return new_func
