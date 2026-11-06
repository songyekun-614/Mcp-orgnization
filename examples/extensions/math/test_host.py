"""Agent test declaration; Manager executes this only after local approval."""
import asyncio
import module


class Registry:
    def tool(self, name, description):
        def decorate(function):
            self.function = function
            return function
        return decorate


registry = Registry()
module.register(registry)
assert asyncio.run(registry.function(2, 3)) == {'sum': 5}
assert asyncio.run(registry.function(-2, 3)) == {'sum': 1}
