
from __future__ import annotations

import math

from .validation_base import ValidationBase
from .validation_helper import ValidationContext, ValidationOutput


class ParameterValidation(ValidationBase):
    """Validate configured parameter names and XML attribute values per scenario."""

    def validate(self, context: ValidationContext) -> ValidationOutput:
        errors = []
        schemas = self.cfg.get("scenarios", {})
        scenarios = context.route_element.findall("./scenarios/scenario")
        if not scenarios:
            errors.append("selected route has no scenarios")
        seen_names = set()
        for scenario_index, scenario in enumerate(scenarios):
            name = scenario.get("name", f"scenario[{scenario_index}]")
            scenario_type = scenario.get("type", "")
            if not scenario.get("name"):
                errors.append(f"scenario[{scenario_index}] has no name")
            elif name in seen_names:
                errors.append(f"{name} ({scenario_type}): duplicate scenario name")
            seen_names.add(name)
            schema = schemas.get(scenario_type)
            if not isinstance(schema, dict):
                errors.append(f"{name} ({scenario_type}): no parameter schema is configured")
                continue

            required = set(schema.get("required", []))
            optional = set(schema.get("optional", []))
            if required & optional:
                errors.append(f"{name} ({scenario_type}): schema repeats required and optional fields")
            allowed = required | optional
            supplied = {
                element.tag for element in list(scenario)
                if element.tag not in {"trigger_point", "other_actor"}
            }
            for parameter in sorted(required - supplied):
                errors.append(f"{name} ({scenario_type}): missing required parameter '{parameter}'")
            for parameter in sorted(supplied - allowed):
                errors.append(f"{name} ({scenario_type}): unsupported parameter '{parameter}'")

            for element in list(scenario):
                if element.tag not in supplied or element.tag not in allowed:
                    continue
                errors.extend(
                    f"{name} ({scenario_type}).{element.tag}: {message}"
                    for message in self._validate_parameter(element.tag, element.attrib)
                )

        return ValidationOutput(valid=not errors, errors=errors)

    def _validate_parameter(self, name: str, attributes: dict[str, str]) -> list[str]:
        errors = []
        location_parameters = set(self.cfg.get("location_parameters", []))
        range_parameters = set(self.cfg.get("range_parameters", []))
        if name in location_parameters:
            expected = {"x", "y", "z"}
        elif name in range_parameters:
            expected = {"from", "to"}
        else:
            expected = {"value"}
        if set(attributes) != expected:
            return [f"expected attributes {sorted(expected)}, got {sorted(attributes)}"]

        if name in location_parameters:
            for axis, value in attributes.items():
                self._check_number(value, f"{name}.{axis}", errors)
            return errors

        if name in range_parameters:
            low = self._check_number(attributes["from"], f"{name}.from", errors)
            high = self._check_number(attributes["to"], f"{name}.to", errors)
            if low is not None and high is not None and low > high:
                errors.append("requires from <= to")
            if name in self.cfg.get("positive_parameters", []):
                if low is not None and low <= 0:
                    errors.append("from must be positive")
                if high is not None and high <= 0:
                    errors.append("to must be positive")
            return errors

        value = attributes["value"]
        enums = self.cfg.get("enums", {})
        if name in enums:
            if value not in enums[name]:
                errors.append(f"must be one of {sorted(enums[name])}, got {value!r}")
            return errors
        if name in self.cfg.get("string_parameters", []):
            if not value.strip():
                errors.append("must not be empty")
            return errors
        if name in self.cfg.get("boolean_parameters", []):
            if value.lower() not in {"true", "false"}:
                errors.append("must be 'true' or 'false'")
            return errors

        number = self._check_number(value, name, errors)
        if name in self.cfg.get("positive_parameters", []) and number is not None and number <= 0:
            errors.append("must be positive")
        return errors

    @staticmethod
    def _check_number(value: str, label: str, errors: list[str]) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            errors.append(f"{label} must be numeric")
            return None
        if not math.isfinite(number):
            errors.append(f"{label} must be finite")
            return None
        return number