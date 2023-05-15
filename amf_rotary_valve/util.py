from typing import Any, Awaitable, Callable


def aexit_handler(func: Callable[[Any], Awaitable[None]], /):
  async def new_func(self, exc_type, exc_value, traceback):
    exceptions = list[BaseException]()

    if exc_type:
      exceptions.append(exc_value)

    try:
      await func(self)
    except BaseException as e:
      exceptions.append(e)

    if len(exceptions) > 1:
      raise BaseExceptionGroup("Asynchronous exit handler", exceptions)
    elif exceptions:
      raise exceptions[0]

  return new_func
