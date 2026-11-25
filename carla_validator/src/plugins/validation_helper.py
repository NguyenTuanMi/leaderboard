from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class ValidationContext:
    route_file: str
    route_id: str
    route_element: ET.Element


@dataclass
class ValidationOutput:
    """The result of a validation plugin."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)









