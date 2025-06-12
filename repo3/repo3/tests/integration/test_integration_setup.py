import sys
import logging
from unittest.mock import MagicMock, patch

from homeassistant.components.websocket_api import DOMAIN as WEBSOCKET_DOMAIN
from homeassistant.core import HomeAssistant
import pytest

from custom_components.hacs.base import HacsBase

from tests.common import create_config_entry, get_hacs
from tests.conftest import SnapshotFixture

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_integration_setup(
    hass: HomeAssistant,
    snapshots: SnapshotFixture,
):
    logger.info("Starting test_integration_setup")
    config_entry = create_config_entry()
    logger.info(f"Created config entry: {config_entry.entry_id}")
    
    hass.data.pop("custom_components", None)
    logger.info("Cleared custom_components from hass.data")
    
    config_entry.add_to_hass(hass)
    logger.info("Added config entry to hass")
    
    setup_result = await hass.config_entries.async_setup(config_entry.entry_id)
    logger.info(f"Config entry setup result: {setup_result}")
    assert setup_result
    await hass.async_block_till_done()
    logger.info("Hass async operations completed")

    hacs: HacsBase = get_hacs(hass)
    logger.info(f"HACS system status - disabled: {hacs.system.disabled}, stage: {hacs.stage}")
    assert not hacs.system.disabled
    assert hacs.stage == "running"

    websocket_commands = [
        command for command in hass.data[WEBSOCKET_DOMAIN] if command.startswith("hacs/")
    ]
    logger.info(f"Found HACS websocket commands: {websocket_commands}")

    await snapshots.assert_hacs_data(
        hacs,
        "test_integration_setup.json",
        {
            "websocket_commands": websocket_commands,
        },
    )
    logger.info("Snapshot assertion completed successfully")


async def test_integration_setup_with_custom_updater(
    hass: HomeAssistant,
    snapshots: SnapshotFixture,
    caplog: pytest.LogCaptureFixture,
):
    logger.info("Starting test_integration_setup_with_custom_updater")
    config_entry = create_config_entry()
    logger.info(f"Created config entry: {config_entry.entry_id}")
    
    hass.data.pop("custom_components", None)
    logger.info("Cleared custom_components from hass.data")
    
    config_entry.add_to_hass(hass)
    logger.info("Added config entry to hass")
    
    with patch.dict(
        sys.modules,
        {
            **sys.modules,
            # Pretend custom_updater is loaded
            "custom_components.custom_updater": MagicMock(),
        },
    ):
        logger.info("Patched sys.modules to include custom_updater")
        setup_result = await hass.config_entries.async_setup(config_entry.entry_id)
        logger.info(f"Config entry setup result: {setup_result}")
        assert not setup_result
        await hass.async_block_till_done()
        logger.info("Hass async operations completed")

    hacs: HacsBase = get_hacs(hass)
    logger.info(f"HACS system status - disabled_reason: {hacs.system.disabled_reason}")
    assert hacs.system.disabled_reason == "constrains"

    expected_message = "HACS cannot be used with custom_updater. To use HACS you need to remove custom_updater from `custom_components`"
    logger.info(f"Checking for expected message in logs: {expected_message}")
    assert expected_message in caplog.text
    logger.info("Found expected message in logs")

    await snapshots.assert_hacs_data(
        hacs,
        "test_integration_setup_with_custom_updater.json",
        {},
    )
    logger.info("Snapshot assertion completed successfully")
