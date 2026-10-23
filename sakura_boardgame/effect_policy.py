"""Persistent effect lifecycle policy, independent of navigation and turn switching.

Only the existing engine completion sites call tick_completed_action. Opening a
picker, route/landing subdecisions and forfeited skip turns do not tick; black
train suspends ordinary effects. Acquisition adds one tick because its ordinary
action will complete immediately afterwards. One-shot items are consumed by the
specific successful operation, not by this duration policy.
"""
from typing import Any, MutableMapping

EFFECT_POLICY = {
    "adventure_left": {"clock": "own_completed_ordinary_action", "paused_in_train": True},
    "buff_left": {"clock": "own_completed_ordinary_action", "paused_in_train": True},
    "rent_shield": {"consume": "actual_nonexempt_rent_payment"},
    "upgrade_coupon": {"consume": "successful_upgrade"},
    "lucky_charm": {"consume": "first_failed_game_check"},
    "pick_dice": {"consume": "validated_custom_die_choice", "cap": 1},
}


def adventure_active(flags: MutableMapping[str, Any], *, in_train: bool) -> bool:
    return not in_train and int(flags.get("adventure_left") or 0) > 0


def acquire_duration(flags: MutableMapping[str, Any], key: str, future_actions: int) -> None:
    if key not in ("adventure_left", "buff_left"):
        raise ValueError(f"not a duration effect: {key}")
    flags[key] = int(future_actions) + 1


def consume_effect(flags: MutableMapping[str, Any], key: str, *, trigger: str) -> bool:
    """Consume a held one-shot at its validated operation, never on a clock tick.

    Callers still validate affordability/exemptions/check outcome before invoking.
    An explicit trigger prevents a new caller silently inventing a lifecycle.
    """
    if EFFECT_POLICY.get(key, {}).get("consume") != trigger:
        raise ValueError(f"invalid consumption trigger for {key}: {trigger}")
    if not flags.get(key):
        return False
    flags[key] = False
    return True


def tick_completed_action(flags: MutableMapping[str, Any], *, in_train: bool) -> list[str]:
    """Mutate only duration fields; return existing expiration messages."""
    if in_train:
        return []
    messages = []
    if int(flags.get("adventure_left") or 0) > 0:
        flags["adventure_left"] -= 1
        if not flags["adventure_left"]:
            messages.append("冒险契约到期，普通现金收支恢复原倍率。")
    if flags.get("buff"):
        flags["buff_left"] = int(flags.get("buff_left") or 0) - 1
        if flags["buff_left"] <= 0:
            label = {"gain": "现金增益", "loss": "节流道具", "loss_up": "现金减益"}.get(flags["buff"], "增益")
            flags["buff"] = None
            flags["buff_left"] = 0
            messages.append(f"{label}用完了，现金收支恢复原样。")
    return messages
