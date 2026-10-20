"""External namespace import for cross-repo mapping."""
import flow.demo.sdk as sdk


def use_sdk():
    return sdk.client()
