"""Small Agent-authored, pure computation adapter for the generic host."""


def register(registry):
    @registry.tool('ext_add', 'Add two integers')
    async def add(x: int, y: int) -> dict:
        return {'sum': x + y}
