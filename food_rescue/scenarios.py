"""Curated, repeatable evaluation cases; no real organizations or live traffic."""

from .simulation import make_donation, make_world


def scenarios() -> list[dict]:
    cases = []
    for name in ("straightforward", "split_donation", "missing_window", "no_driver",
                 "incompatible_food", "deadline_impossible", "confirmation_pending",
                 "driver_cancellation", "existing_booking", "capacity_shortage", "scarce_transport"):
        world, donation = make_world(), make_donation()
        expected = {"status": "scheduled", "min_meals": 60}
        event = None
        if name == "straightforward":
            donation["quantity"] = 15
            expected["min_meals"] = 15
        elif name == "missing_window":
            donation["pickup_end"] = None
            expected = {"status": "needs_information", "min_meals": 0}
        elif name == "no_driver":
            for driver in world["drivers"]:
                driver["available"] = False
            expected = {"status": "no_feasible_plan", "min_meals": 0}
        elif name == "incompatible_food":
            donation["food_type"] = "unsupported_food"
            expected = {"status": "no_feasible_plan", "min_meals": 0}
        elif name == "deadline_impossible":
            donation["deliver_by"] = 900
            expected = {"status": "no_feasible_plan", "min_meals": 0}
        elif name == "confirmation_pending":
            for shelter in world["shelters"]:
                shelter["confirmed"] = False
            expected = {"status": "awaiting_confirmation", "min_meals": 0}
        elif name == "driver_cancellation":
            # d1 is the nearest initial driver. All replans must exclude it.
            event = "d1"
        elif name == "existing_booking":
            world["reservations"] = [{"donation_id": "other-donation", "shelter_id": "s1",
                                      "driver_id": "d1", "quantity": 20,
                                      "pickup_at": 900, "deliver_at": 910}]
        elif name == "capacity_shortage":
            for shelter in world["shelters"]:
                shelter["capacity"] = 5
            expected["min_meals"] = 25  # Five drivers, five direct trips.
        elif name == "scarce_transport":
            # Greedy wastes the large vehicle on the nearby 20-meal recipient.
            # A better plan sends d1's 40 meals to SoMa and d2's 20 to Mission.
            for shelter in world["shelters"]:
                shelter["capacity"] = {"s1": 20, "s2": 40}.get(shelter["id"], 0)
            for driver in world["drivers"]:
                driver["available"] = driver["id"] in {"d1", "d2"}
            world["drivers"][1]["capacity"] = 20
        cases.append({"name": name, "inputs": {"world": world, "donation": donation,
                      "cancel_driver_id": event}, "outputs": expected,
                      "baseline_expected_pass": name != "scarce_transport",
                      "split": "test" if name in {"existing_booking", "capacity_shortage", "deadline_impossible"} else "dev"})
    return cases
