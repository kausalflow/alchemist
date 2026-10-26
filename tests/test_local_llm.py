"""`local:` models: a model server in the plant's own network that speaks the OpenAI chat API (vLLM, Ollama,
llama.cpp, LM Studio, TGI), tested against an in-process server that answers with a tool call."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from alchemist import llm
from alchemist.config import Settings


@pytest.fixture()
def server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append({"path": self.path, "auth": self.headers.get("authorization"), "body": body})
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "search_catalog", "arguments": json.dumps({"text": "melt temperature"})}}]}
            data = json.dumps({"id": "c1", "object": "chat.completion", "created": 0, "model": body["model"],
                               "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1", seen
    httpd.shutdown()


def settings(url):
    s = Settings()
    s.local_llm_url, s.allowed_providers = url, ["local"]
    return s


def test_local_model_calls_tools_on_the_local_server(server, monkeypatch):
    url, seen = server
    monkeypatch.setenv("ALC_LOCAL_LLM_API_KEY", "lan-key")
    m = llm.PydanticAIModel(settings(url), "local:qwen3:32b", effort="high")
    assert m.spec == "local:qwen3:32b"  # the model id may itself contain ':' (Ollama tags)
    out = m.create(system="You plan investigations.", messages=[{"role": "user", "content": "Why was yield low?"}],
                   tools=[{"name": "search_catalog", "description": "search datasets",
                           "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}}}])
    assert [b for b in out["content"] if b["type"] == "tool_use"][0]["input"] == {"text": "melt temperature"}
    req = seen[0]
    assert req["path"] == "/v1/chat/completions" and req["auth"] == "Bearer lan-key"
    assert req["body"]["model"] == "qwen3:32b" and req["body"]["tools"][0]["function"]["name"] == "search_catalog"


def test_local_needs_an_address_and_the_allow_list():
    s = settings(None)
    with pytest.raises(ValueError, match="ALC_LOCAL_LLM_URL"):
        llm.PydanticAIModel(s, "local:llama3.3")
    s = settings("http://127.0.0.1:1/v1")
    s.allowed_providers = ["bedrock"]
    with pytest.raises(llm.ProviderNotAllowed):
        llm.PydanticAIModel(s, "local:llama3.3")
    assert llm.split_spec("local:gpt-oss:120b") == ("local", "gpt-oss:120b")
