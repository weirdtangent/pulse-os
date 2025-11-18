"""MQTT discovery message builders for Home Assistant."""

from __future__ import annotations

from typing import Any


def build_button_entity(
    name: str,
    unique_id: str,
    command_topic: str,
    sanitized_hostname: str,
    payload_press: str = "press",
    entity_category: str | None = None,
    availability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Home Assistant button entity definition.
    
    Args:
        name: Display name of the button.
        unique_id: Unique identifier for the entity.
        command_topic: MQTT topic to publish commands to.
        sanitized_hostname: Sanitized hostname for entity ID.
        payload_press: Payload to send when button is pressed.
        entity_category: Optional entity category (e.g., "config").
        availability: Optional availability configuration.
    
    Returns:
        Button entity definition dictionary.
    """
    entity: dict[str, Any] = {
        "platform": "button",
        "name": name,
        "default_entity_id": f"button.{sanitized_hostname}.{unique_id.split('_')[-1]}",
        "cmd_t": command_topic,
        "pl_press": payload_press,
        "unique_id": unique_id,
    }
    if entity_category:
        entity["entity_category"] = entity_category
    if availability:
        entity["availability"] = availability
    return entity


def build_number_entity(
    name: str,
    unique_id: str,
    command_topic: str,
    state_topic: str,
    sanitized_hostname: str,
    min_value: int = 0,
    max_value: int = 100,
    step: int = 1,
    unit_of_measurement: str | None = None,
    icon: str | None = None,
    entity_category: str | None = None,
) -> dict[str, Any]:
    """Build a Home Assistant number entity definition.
    
    Args:
        name: Display name of the number control.
        unique_id: Unique identifier for the entity.
        command_topic: MQTT topic to publish commands to.
        state_topic: MQTT topic to publish state to.
        sanitized_hostname: Sanitized hostname for entity ID.
        min_value: Minimum value.
        max_value: Maximum value.
        step: Step size.
        unit_of_measurement: Optional unit of measurement (e.g., "%").
        icon: Optional icon (e.g., "mdi:volume-high").
        entity_category: Optional entity category (e.g., "config").
    
    Returns:
        Number entity definition dictionary.
    """
    entity: dict[str, Any] = {
        "platform": "number",
        "name": name,
        "default_entity_id": f"number.{sanitized_hostname}_{unique_id.split('_')[-1]}",
        "cmd_t": command_topic,
        "stat_t": state_topic,
        "unique_id": unique_id,
        "min": min_value,
        "max": max_value,
        "step": step,
    }
    if unit_of_measurement:
        entity["unit_of_meas"] = unit_of_measurement
    if icon:
        entity["icon"] = icon
    if entity_category:
        entity["entity_category"] = entity_category
    return entity


def build_sensor_entity(
    name: str,
    unique_id: str,
    state_topic: str,
    sanitized_hostname: str,
    unit_of_measurement: str | None = None,
    device_class: str | None = None,
    state_class: str | None = None,
    icon: str | None = None,
    expire_after: int | None = None,
) -> dict[str, Any]:
    """Build a Home Assistant sensor entity definition.
    
    Args:
        name: Display name of the sensor.
        unique_id: Unique identifier for the entity.
        state_topic: MQTT topic to read state from.
        sanitized_hostname: Sanitized hostname for entity ID.
        unit_of_measurement: Optional unit of measurement.
        device_class: Optional device class.
        state_class: Optional state class.
        icon: Optional icon.
        expire_after: Optional expiration time in seconds.
    
    Returns:
        Sensor entity definition dictionary.
    """
    entity: dict[str, Any] = {
        "platform": "sensor",
        "name": name,
        "default_entity_id": f"sensor.{sanitized_hostname}_{unique_id.split('_')[-1]}",
        "stat_t": state_topic,
        "unique_id": unique_id,
    }
    if unit_of_measurement:
        entity["unit_of_meas"] = unit_of_measurement
    if device_class:
        entity["dev_cla"] = device_class
    if state_class:
        entity["stat_cla"] = state_class
    if icon:
        entity["ic"] = icon
    if expire_after:
        entity["exp_aft"] = expire_after
    return entity

