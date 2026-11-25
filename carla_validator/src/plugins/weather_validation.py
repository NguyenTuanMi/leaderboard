

import math

from .validation_base import ValidationBase
from .validation_helper import ValidationContext, ValidationOutput

class WeatherValidation(ValidationBase):

    def validate(self, context: ValidationContext) -> ValidationOutput:
        weather_group = context.route_element.find("weathers")
        if weather_group is None:
            return ValidationOutput(valid=True, details={"weather_entries": 0})

        errors = []
        allowed_attributes = set(self.cfg.get("weather_attributes", []))
        previous_percentage = -1.0
        entries = weather_group.findall("weather")
        for index, item in enumerate(entries):
            raw_percentage = item.get("route_percentage")
            try:
                percentage = float(raw_percentage)
            except (TypeError, ValueError):
                errors.append(f"weather[{index}].route_percentage must be numeric")
            else:
                if not math.isfinite(percentage) or not 0 <= percentage <= 100:
                    errors.append(f"weather[{index}].route_percentage must be within 0..100")
                else:
                    if percentage < previous_percentage:
                        errors.append("weather route_percentage values must be sorted")
                    previous_percentage = percentage
            for name, value in item.attrib.items():
                if name == "route_percentage":
                    continue
                if name not in allowed_attributes:
                    errors.append(f"weather[{index}] has unsupported attribute '{name}'")
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    errors.append(f"weather[{index}].{name} must be numeric")
                    continue
                if not math.isfinite(number):
                    errors.append(f"weather[{index}].{name} must be finite")

        if not entries:
            errors.append("weathers must contain at least one weather entry")
        return ValidationOutput(
            valid=not errors,
            errors=errors,
            details={"weather_entries": len(entries)},
        )