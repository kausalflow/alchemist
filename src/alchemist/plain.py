"""Plain words for what went wrong, so an engineer reads what happened and what to do instead of an error code.

The technical text is kept next to it (shown folded away as "Details for IT"): it is what IT needs to fix it.
"""

from __future__ import annotations

import re

# First match wins: (pattern on the technical text, what happened, what to do).
_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^interrupted", re.IGNORECASE),
     "The analysis stopped unexpectedly, for example because the server restarted.",
     "Nothing is lost: press Resume to carry on from its last step."),
    (re.compile(r"ExpiredToken|token (has )?expired|security token included in the request is expired|"
                r"SSO session|Token has expired and refresh failed|sso.*login", re.IGNORECASE),
     "The sign-in Alchemist uses for the AI model has expired.",
     "On a laptop, sign in to AWS again (for example aws sso login) and ask again. On the hosted service, tell IT."),
    (re.compile(r"NoCredentials|Unable to locate credentials|could not find (the )?profile|ProfileNotFound|"
                r"UnrecognizedClient|InvalidClientTokenId|SignatureDoesNotMatch", re.IGNORECASE),
     "Alchemist has no working sign-in for the AI model.",
     "Check the AWS profile in the settings file, or tell IT that the service has no model credentials."),
    (re.compile(r"AccessDenied|not authorized to perform|don't have access to the model|"
                r"You don't have access|Forbidden|status.?code.?403", re.IGNORECASE),
     "Alchemist was not allowed to use the AI model.",
     "Ask IT to give Alchemist access to the model in Amazon Bedrock (or the provider you use)."),
    (re.compile(r"model identifier is invalid|ResourceNotFound.*model|model.*not found|"
                r"on-demand throughput isn.t supported|inference profile|Unknown model|invalid model", re.IGNORECASE),
     "The AI model named in the settings does not exist for this account or region.",
     "Ask an admin to check the models on the Models and limits page (alchemist models check tests them)."),
    (re.compile(r"Throttl|TooManyRequests|rate.?limit|Too many requests|status.?code.?429|quota", re.IGNORECASE),
     "The AI model is busy: too many requests at once.",
     "Wait a few minutes and try again. If it happens often, ask IT to raise the model quota."),
    (re.compile(r"overloaded|ServiceUnavailable|InternalServerError|status.?code.?(5\d\d)|"
                r"provider is unavailable|ModelNotReady", re.IGNORECASE),
     "The AI model service was not available.",
     "Nothing is lost. Try again, or press Resume, in a few minutes."),
    (re.compile(r"CERTIFICATE_VERIFY_FAILED|SSLError|SSL: |certificate verify", re.IGNORECASE),
     "A secure connection to the AI model could not be set up, usually because of a company proxy.",
     "Tell IT; they may need to add the company certificate to Alchemist's settings."),
    (re.compile(r"EndpointConnectionError|ConnectionError|ConnectTimeout|ReadTimeout|timed out|Timeout|"
                r"Name or service not known|getaddrinfo|Connection (refused|reset)|network", re.IGNORECASE),
     "Alchemist could not reach the AI model over the network.",
     "Check the network or VPN and try again. On the hosted service, tell IT."),
    (re.compile(r"budget: (max steps|steps)|tool calls", re.IGNORECASE),
     "The question used up its allowance of steps before it finished.",
     "Ask a narrower question, or ask an admin to raise the step limit."),
    (re.compile(r"budget: .*tokens|max_tokens|output tokens", re.IGNORECASE),
     "The question used up its allowance of model text before it finished.",
     "Ask a narrower question, or ask an admin to raise the token limit."),
    (re.compile(r"budget: time limit", re.IGNORECASE),
     "The question reached its time limit.",
     "Give it more time from the run page, or ask a narrower question."),
    (re.compile(r"ended without a plan", re.IGNORECASE),
     "Alchemist could not finish drafting a plan for this question.",
     "Try asking again, perhaps with more detail on the line, dates or data. If it happens again, tell IT."),
]

DEFAULT = ("Something went wrong while Alchemist was working on this.",
           "Try again. If it happens again, send the details below to IT.")


def explain(text: str | None) -> dict:
    """{message, action, details} for a technical failure text."""
    t = text or ""
    for pattern, message, action in _RULES:
        if pattern.search(t):
            return {"message": message, "action": action, "details": t}
    return {"message": DEFAULT[0], "action": DEFAULT[1], "details": t}
