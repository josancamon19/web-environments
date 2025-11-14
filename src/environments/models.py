from dataclasses import dataclass, field
from typing import Any, Optional

from utils.normalize_url import normalize_url_for_matching


@dataclass
class HarKeyValue:
    name: str
    value: str

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary format."""
        return {"name": self.name, "value": self.value}


@dataclass
class HarRequestPostData:
    mimeType: str
    text: str
    params: list[Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "mimeType": self.mimeType,
            "text": self.text,
            "params": self.params,
        }


@dataclass
class HarResponseContent:
    size: int
    mimeType: str
    text: str
    compression: int | None = None
    encoding: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            "size": self.size,
            "mimeType": self.mimeType,
            "text": self.text,
        }
        if self.compression is not None:
            result["compression"] = self.compression
        if self.encoding is not None:
            result["encoding"] = self.encoding
        return result


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

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            "status": self.status,
            "statusText": self.statusText,
            "httpVersion": self.httpVersion,
            "content": self.content.to_dict(),
            "headersSize": self.headersSize,
            "bodySize": self.bodySize,
            "redirectURL": self.redirectURL,
            "cookies": [c.to_dict() for c in self.cookies],
            "headers": [h.to_dict() for h in self.headers],
        }
        if self.transferSize is not None:
            result["transferSize"] = self.transferSize
        return result


@dataclass
class HarRequest:
    method: str
    url: str
    headers: list[HarKeyValue] = field(default_factory=list)
    cookies: list[HarKeyValue] = field(default_factory=list)
    postData: HarRequestPostData | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            "method": self.method,
            "url": self.url,
            "headers": [h.to_dict() for h in self.headers],
            "cookies": [c.to_dict() for c in self.cookies],
        }
        if self.postData is not None:
            result["postData"] = self.postData.to_dict()
        return result


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

    def to_dict(self) -> dict[str, Any]:
        """Convert to full dictionary format."""
        return {
            "request": self.request.to_dict(),
            "response": self.response.to_dict(),
        }

    def to_lm_match_format(self) -> dict[str, Any]:
        """Convert to the specific format needed for LM matching."""
        return {
            "method": self.request.method,
            "url": normalize_url_for_matching(self.request.url),
            "headers": {h.name: h.value for h in self.request.headers},
            "postData": {
                "mimeType": self.request.postData.mimeType,
                "text": self.request.postData.text,
            }
            if self.request.postData
            else None,
            "responseMimeType": self.response.content.mimeType,
        }


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


@dataclass
class CandidateEntryMetadata:
    match_score: int
    body_score: int
    headers_score: int
    matches_all: bool = False


@dataclass
class CandidateEntry:
    idx: int
    entry: HarEntry
    metadata: CandidateEntryMetadata
