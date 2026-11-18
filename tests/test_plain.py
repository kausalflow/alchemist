"""Plain words for failures."""

import pytest

from alchemist import plain


@pytest.mark.parametrize("text, start", [
    (("planning ended without a plan: error: ClientError: An error occurred (ExpiredTokenException) when calling "
      "the InvokeModel operation: The security token included in the request is expired"), "The sign-in"),
    (("error: ClientError: An error occurred (AccessDeniedException) when calling the Converse operation: You "
      "don't have access to the model with the specified model ID."), "Alchemist was not allowed"),
    ("error: ValidationException: The provided model identifier is invalid.", "The AI model named"),
    ("error: ThrottlingException: Too many requests, please wait before trying again.", "The AI model is busy"),
    ("paused: the model provider is unavailable (ServiceUnavailableException: x)", "The AI model service"),
    ("error: SSLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", "A secure connection"),
    ("error: EndpointConnectionError: Could not connect to the endpoint URL", "Alchemist could not reach"),
    ("error: UnicodeEncodeError: 'charmap' codec can't encode character '\\u2265' in position 12",
     "A special character"),
    ("interrupted: the worker stopped", "The analysis stopped unexpectedly"),
    ("planning ended without a plan: budget: max steps (60) reached", "The question used up its allowance of steps"),
    ("planning ended without a plan: done", "Alchemist could not finish drafting"),
    ("KeyError: 'x'", "Something went wrong"),
])
def test_explain(text, start):
    got = plain.explain(text)
    assert got["message"].startswith(start) and got["action"] and got["details"] == text


def test_empty():
    assert plain.explain(None)["message"] == plain.DEFAULT[0]
