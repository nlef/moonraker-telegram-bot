from unittest.mock import MagicMock

from bot.notifications import Notifier  # type: ignore


def make_notifier(height=5.0, percent=0):
    config = MagicMock()
    config.secrets.chat_id = 123
    config.notifications.enabled = True
    config.notifications.percent = percent
    config.notifications.height = height
    config.notifications.interval = 0
    config.notifications.notify_groups = []
    config.notifications.group_only = False
    config.bot_config.max_upload_file_size = 50
    config.bot_config.debug = False
    config.telegram_ui.progress_update_message = True
    config.telegram_ui.silent_progress = False
    config.telegram_ui.silent_commands = False
    config.telegram_ui.silent_status = False
    config.telegram_ui.pin_status_single_message = False
    config.telegram_ui.status_message_m117_update = False
    config.telegram_ui.status_update_button = False
    config.status_message_content.content = []

    klippy = MagicMock()
    klippy.printing = True
    klippy.printing_duration = 100.0

    return Notifier(config, MagicMock(), klippy, MagicMock(), MagicMock(), None)


def test_height_notification_triggers_at_threshold():
    n = make_notifier(height=2.5)
    n._schedule_notification = MagicMock()

    # must see Z below threshold before firing
    n.schedule_notification(position_z=0.2)
    n.schedule_notification(position_z=2.5)
    assert n._schedule_notification.call_count == 1

    n.schedule_notification(position_z=3.0)
    n.schedule_notification(position_z=5.0)
    assert n._schedule_notification.call_count == 2

    n.schedule_notification(position_z=6.0)
    n.schedule_notification(position_z=7.5)
    assert n._schedule_notification.call_count == 3

    # below next threshold — no new notification
    n.schedule_notification(position_z=9.0)
    assert n._schedule_notification.call_count == 3

    # same height again — no duplicate
    n.schedule_notification(position_z=7.5)
    assert n._schedule_notification.call_count == 3


def test_height_notification_with_real_layers():
    """With 0.2mm layers Z never lands exactly on 2.5 — must still trigger."""
    n = make_notifier(height=2.5)
    n._schedule_notification = MagicMock()
    layer = 0.2

    # layers up to 2.4 — below first threshold
    z = layer
    while z < 2.5:
        n.schedule_notification(position_z=round(z, 2))
        z += layer
    assert n._schedule_notification.call_count == 0

    # 2.6 crosses 2.5
    n.schedule_notification(position_z=2.6)
    assert n._schedule_notification.call_count == 1
    assert n._last_height == 2.5  # snapped, not drifted to 2.6

    # continue up to 4.8 — below 5.0 threshold
    z = 2.8
    while z < 5.0:
        n.schedule_notification(position_z=round(z, 2))
        z += layer
    assert n._schedule_notification.call_count == 1

    n.schedule_notification(position_z=5.0)
    assert n._schedule_notification.call_count == 2


def test_height_notification_rejects_wild_z_jumps():
    """Z far above threshold (start gcode, travel moves) should not fire."""
    n = make_notifier(height=2.5)
    n._schedule_notification = MagicMock()

    # purge at low Z then Z jumps high — too far from threshold
    n.schedule_notification(position_z=0.3)
    n.schedule_notification(position_z=4.5)
    assert n._schedule_notification.call_count == 0

    # Z drops to first layer, rises normally
    n.schedule_notification(position_z=0.2)
    n.schedule_notification(position_z=2.6)
    assert n._schedule_notification.call_count == 1

    # wild Z jump mid-print — too far from next threshold (5.0)
    n.schedule_notification(position_z=0.4)
    n.schedule_notification(position_z=50.6)
    assert n._schedule_notification.call_count == 1

    # back to normal layers, fires at 5.0
    n.schedule_notification(position_z=3.0)
    n.schedule_notification(position_z=5.0)
    assert n._schedule_notification.call_count == 2


def test_height_notification_sequential_objects():
    """Z drops back to first layer between objects — should restart notifications."""
    n = make_notifier(height=2.5)
    n._schedule_notification = MagicMock()

    layer = 0.2
    obj_height = 10.0

    # first object
    z = layer
    while z <= obj_height:
        n.schedule_notification(position_z=round(z, 2))
        z += layer
    # fired at 2.5, 5.0, 7.5, 10.0
    assert n._schedule_notification.call_count == 4

    # Z drops to first layer for second object
    n.schedule_notification(position_z=layer)

    # notifications restart — climb just below first threshold
    z = layer
    while z < 2.5:
        n.schedule_notification(position_z=round(z, 2))
        z += layer
    assert n._schedule_notification.call_count == 4  # no new

    n.schedule_notification(position_z=2.6)
    assert n._schedule_notification.call_count == 5


def test_height_notification_guards():
    n = make_notifier(height=5)
    n._schedule_notification = MagicMock()

    n._klippy.printing = False
    n.schedule_notification(position_z=5.0)
    assert n._schedule_notification.call_count == 0

    n._klippy.printing = True
    n._klippy.printing_duration = 0.0
    n.schedule_notification(position_z=5.0)
    assert n._schedule_notification.call_count == 0
