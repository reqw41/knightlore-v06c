#!/bin/sh
# curl-хелпер MCP Emu80: tools/emu80_mcp.sh <tool> '<json-аргументы>' [порт]
# Примеры: screen '{}' ; kbd_send '{"keys":["0"],"action":"tap","duration_ms":200}' ; mem_read '{"addr":"0xA000","len":64}'
TOOL=$1; ARGS=${2:-'{}'}; PORT=${3:-19785}
H='-H Content-Type:application/json -H Accept:application/json,text/event-stream'
curl -s $H -X POST http://127.0.0.1:$PORT/mcp -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"kl","version":"0"}}}' >/dev/null
curl -s $H -X POST http://127.0.0.1:$PORT/mcp -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"$TOOL\",\"arguments\":$ARGS}}"
