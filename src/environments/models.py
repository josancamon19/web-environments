from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CandidateEntryMetadata:
    match_score: int
    body_score: int
    headers_score: int
    matches_all: bool = False


@dataclass
class CandidateEntry:
    idx: int
    entry: dict
    metadata: CandidateEntryMetadata


@dataclass
class HarKeyValue:
    name: str
    value: str


@dataclass
class HarRequestPostData:
    mimeType: str
    text: str
    params: list[Any]


@dataclass
class HarResponseContent:
    size: int
    mimeType: str
    text: str
    compression: int | None = None
    encoding: str | None = None


@dataclass
class HarResponse:
    status: int
    statusText: str
    httpVersion: str
    content: HarResponseContent
    headersSize: int
    bodySize: int
    redirectURL: str
    transferSize: int | None = None
    cookies: list[HarKeyValue] = field(default_factory=list)
    headers: list[HarKeyValue] = field(default_factory=list)


@dataclass
class HarRequest:
    method: str
    url: str
    headers: list[HarKeyValue] = field(default_factory=list)
    cookies: list[HarKeyValue] = field(default_factory=list)
    postData: HarRequestPostData | None = None


@dataclass
class HarEntry:
    # pageref: str
    # started_datetime: str
    # time: float
    request: HarRequest
    response: HarResponse
    # cache: dict
    # timings: dict
    # server_ip_address: Optional[str] = None
    # server_port: Optional[int] = None
    # security_details: Optional[dict] = None


# Parse functions to convert dict to typed models


def parse_har_key_values(items: list[dict]) -> list[HarKeyValue]:
    """Parse HAR key-value pairs (headers, cookies) from dict."""
    return [
        HarKeyValue(name=item.get("name", ""), value=item.get("value", ""))
        for item in items
    ]


def parse_har_request_post_data(data: Optional[dict]) -> Optional[HarRequestPostData]:
    """Parse HAR request post data from dict."""
    if not data:
        return None
    return HarRequestPostData(
        mimeType=data.get("mimeType", ""),
        text=data.get("text", ""),
        params=data.get("params", []),
    )


def parse_har_response_content(content: dict) -> HarResponseContent:
    """Parse HAR response content from dict."""
    return HarResponseContent(
        size=content.get("size", 0),
        mimeType=content.get("mimeType", ""),
        compression=content.get("compression"),
        text=content.get("text", ""),
        encoding=content.get("encoding"),
    )


def parse_har_response(response: dict) -> HarResponse:
    """Parse HAR response from dict."""
    return HarResponse(
        status=response.get("status", 200),
        statusText=response.get("statusText", ""),
        httpVersion=response.get("httpVersion", ""),
        cookies=parse_har_key_values(response.get("cookies", [])),
        headers=parse_har_key_values(response.get("headers", [])),
        content=parse_har_response_content(response.get("content", {})),
        headersSize=response.get("headersSize", 0),
        bodySize=response.get("bodySize", 0),
        redirectURL=response.get("redirectURL", ""),
        transferSize=response.get("transferSize"),
    )


def parse_har_request(request: dict) -> HarRequest:
    """Parse HAR request from dict."""
    return HarRequest(
        method=request.get("method", "GET"),
        url=request.get("url", ""),
        headers=parse_har_key_values(request.get("headers", [])),
        cookies=parse_har_key_values(request.get("cookies", [])),
        postData=parse_har_request_post_data(request.get("postData")),
    )


def parse_har_entry(entry: dict) -> HarEntry:
    """Parse HAR entry from dict."""
    return HarEntry(
        request=parse_har_request(entry.get("request", {})),
        response=parse_har_response(entry.get("response", {})),
    )
