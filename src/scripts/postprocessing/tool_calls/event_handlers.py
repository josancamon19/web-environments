"""Event handlers for converting raw events into tool calls."""

import json
import urllib.parse
from typing import Dict, Any, Optional, List
from models import ToolCall, ToolCallData
from scripts.postprocessing.tool_calls.element_helpers import (
    create_selector,
    extract_element_context,
    extract_coordinates_from_event,
    merge_coordinates,
)


def find_navigation_after_step(steps_list, current_idx, max_lookahead=10):
    """Find navigation URL after a click or Enter key event."""
    for i in range(
        current_idx + 1, min(current_idx + max_lookahead + 1, len(steps_list))
    ):
        _, event_type, event_data_str, _, _ = steps_list[i]
        if event_type in [
            "state:browser:navigated",
            "state:browser:route_change",
            "state:page:navigate_start",
            "state:page:load",
            "state:page:loaded",
        ]:
            if event_data_str:
                try:
                    event_data = json.loads(event_data_str)
                    url = event_data.get("url", "")
                    if url and url != "about:blank":
                        return url
                except json.JSONDecodeError:
                    pass
    return None


def handle_initial_navigation(
    event_data: Dict[str, Any],
    step_id: int,
    timestamp: str,
) -> Optional[ToolCallData]:
    """Handle initial page navigation."""
    url = event_data.get("url", "")
    if url:
        return ToolCallData(
            type=ToolCall.GO_TO.value,
            params={"url": url},
            step_ids=[step_id],
            timestamp=timestamp,
        )
    return None


def handle_domain_navigation(
    event_data: Dict[str, Any],
    step_id: int,
    timestamp: str,
    tool_calls: List[ToolCallData],
) -> Optional[ToolCallData]:
    """Handle navigation to a different domain."""
    url = event_data.get("url", "")
    if not url or not tool_calls:
        return None

    # Get the last recorded URL
    last_url = None
    for tc in reversed(tool_calls):
        if tc.type == ToolCall.GO_TO.value:
            last_url = tc.params.get("url", "")
            break

    # TODO: consider SPA's as change of go_to? not sure
    if last_url:
        last_domain = urllib.parse.urlparse(last_url).netloc
        new_domain = urllib.parse.urlparse(url).netloc

        # If navigating to a different domain, record it as a GO_TO
        if last_domain != new_domain and new_domain:
            return ToolCallData(
                type=ToolCall.GO_TO.value,
                params={"url": url},
                step_ids=[step_id],
                timestamp=timestamp,
            )
    return None


def handle_mouse_event(
    event_data: Dict[str, Any],
    step_id: int,
    timestamp: str,
    dom_snapshot: str,
    click_buffer: Optional[ToolCallData],
    save_dom_fn,
) -> ToolCallData:
    """Handle mouse/pointer down/up events."""
    if click_buffer is None:
        selector = create_selector(event_data)
        context = extract_element_context(dom_snapshot, event_data)
        dom_state_path = save_dom_fn(step_id, dom_snapshot)
        params = {"selector": selector}
        if context:
            params["selector_details"] = context
        if dom_state_path:
            params["dom_state"] = dom_state_path
        merge_coordinates(params, extract_coordinates_from_event(event_data))
        return ToolCallData(
            type=ToolCall.CLICK.value,
            params=params,
            step_ids=[step_id],
            timestamp=timestamp,
        )
    else:
        click_buffer.step_ids.append(step_id)
        if "dom_state" not in click_buffer.params:
            dom_state_path = save_dom_fn(step_id, dom_snapshot)
            if dom_state_path:
                click_buffer.params["dom_state"] = dom_state_path
        merge_coordinates(
            click_buffer.params,
            extract_coordinates_from_event(event_data),
        )
        return click_buffer


def handle_click_event(
    event_data: Dict[str, Any],
    step_id: int,
    timestamp: str,
    dom_snapshot: str,
    click_buffer: Optional[ToolCallData],
    tool_calls: List[ToolCallData],
    steps_list: List,
    idx: int,
    save_dom_fn,
) -> ToolCallData:
    """Handle actual click event."""
    selector = create_selector(event_data)
    context = extract_element_context(dom_snapshot, event_data)
    dom_state_path = save_dom_fn(step_id, dom_snapshot)
    coordinates_payload = extract_coordinates_from_event(event_data)

    # Check for navigation after this click
    nav_url = find_navigation_after_step(steps_list, idx)

    # If we have a click buffer and it's for the same element, add to it
    if click_buffer and click_buffer.params.get("selector") == selector:
        click_buffer.step_ids.append(step_id)
        # Update context if we have better info
        if context and "selector_details" not in click_buffer.params:
            click_buffer.params["selector_details"] = context
        # Add navigation URL if found
        if nav_url and "navigates_to" not in click_buffer.params:
            click_buffer.params["navigates_to"] = nav_url
        if dom_state_path and "dom_state" not in click_buffer.params:
            click_buffer.params["dom_state"] = dom_state_path
        merge_coordinates(click_buffer.params, coordinates_payload)
        return click_buffer
    elif click_buffer:
        # Different element, save the old buffer and start new
        tool_calls.append(click_buffer)
        params = {"selector": selector}
        if context:
            params["selector_details"] = context
        if nav_url:
            params["navigates_to"] = nav_url
        if dom_state_path:
            params["dom_state"] = dom_state_path
        merge_coordinates(params, coordinates_payload)
        return ToolCallData(
            type=ToolCall.CLICK.value,
            params=params,
            step_ids=[step_id],
            timestamp=timestamp,
        )
    else:
        # No buffer, check if last tool call was same click
        if (
            tool_calls
            and tool_calls[-1].type == ToolCall.CLICK.value
            and tool_calls[-1].params.get("selector") == selector
        ):
            tool_calls[-1].step_ids.append(step_id)
            # Update context if we have better info
            if context and "selector_details" not in tool_calls[-1].params:
                tool_calls[-1].params["selector_details"] = context
            # Add navigation URL if found
            if nav_url and "navigates_to" not in tool_calls[-1].params:
                tool_calls[-1].params["navigates_to"] = nav_url
            if dom_state_path and "dom_state" not in tool_calls[-1].params:
                tool_calls[-1].params["dom_state"] = dom_state_path
            merge_coordinates(tool_calls[-1].params, coordinates_payload)
            return None  # Signal that we updated the last tool call
        else:
            params = {"selector": selector}
            if context:
                params["selector_details"] = context
            if nav_url:
                params["navigates_to"] = nav_url
            if dom_state_path:
                params["dom_state"] = dom_state_path
            merge_coordinates(params, coordinates_payload)
            return ToolCallData(
                type=ToolCall.CLICK.value,
                params=params,
                step_ids=[step_id],
                timestamp=timestamp,
            )


def handle_keydown_event(
    event_data: Dict[str, Any],
    step_id: int,
    timestamp: str,
    typing_buffer: Optional[ToolCallData],
    tool_calls: List[ToolCallData],
    steps_list: List,
    idx: int,
) -> Optional[ToolCallData]:
    """Handle keydown event."""
    key = event_data.get("key", "")

    # Enter key typically submits, so save the buffer first
    if key == "Enter":
        if typing_buffer:
            # Mark that this typing was submitted with Enter
            typing_buffer.params["submit"] = True
            # Check for navigation after Enter key
            nav_url = find_navigation_after_step(steps_list, idx)
            if nav_url:
                typing_buffer.params["navigates_to"] = nav_url
            tool_calls.append(typing_buffer)
            return None  # Signal buffer was flushed
    else:
        # Start accumulating if not already
        if typing_buffer is None:
            # Look for the previous click to get the selector
            prev_selector = None
            for tc in reversed(tool_calls):
                if tc.type == ToolCall.CLICK.value:
                    prev_selector = tc.params.get("selector")
                    break

            if not prev_selector:
                # Try to find selector from a nearby input event
                prev_selector = "*"

            typing_buffer = ToolCallData(
                type=ToolCall.TYPE.value,
                params={"selector": prev_selector, "text": ""},
                step_ids=[],
                timestamp=timestamp,
            )

        typing_buffer.step_ids.append(step_id)
        return typing_buffer

    return typing_buffer


def handle_input_event(
    event_data: Dict[str, Any],
    step_id: int,
    dom_snapshot: str,
    typing_buffer: Optional[ToolCallData],
    tool_calls: List[ToolCallData],
    save_dom_fn,
) -> Optional[ToolCallData]:
    """Handle input event."""
    if typing_buffer:
        typing_buffer.params["text"] = event_data.get("value", "")
        typing_buffer.step_ids.append(step_id)

        # Also update selector and context if we have better info
        selector = create_selector(event_data)
        if selector != "*":
            typing_buffer.params["selector"] = selector

        # Add element context if not already present
        if "selector_details" not in typing_buffer.params:
            context = extract_element_context(dom_snapshot, event_data)
            if context:
                typing_buffer.params["selector_details"] = context
            dom_state_path = save_dom_fn(step_id, dom_snapshot)
            if dom_state_path and "dom_state" not in typing_buffer.params:
                typing_buffer.params["dom_state"] = dom_state_path
            # If still no context, try to get it from the previous click
            elif tool_calls:
                for tc in reversed(tool_calls):
                    if (
                        tc.type == ToolCall.CLICK.value
                        and tc.params.get("selector") == selector
                    ):
                        if "selector_details" in tc.params:
                            typing_buffer.params["selector_details"] = tc.params[
                                "selector_details"
                            ]
                        break

    return typing_buffer
