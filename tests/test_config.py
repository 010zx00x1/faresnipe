from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from faresnipe.config import load_config


class ConfigTest(unittest.TestCase):
    def test_loads_origins_and_expands_routes_with_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "origins.toml"
            config_path.write_text(
                """
[scanner]
provider = "mock"
database_path = "data/test.sqlite3"

[[origins]]
code = "AEP"
name = "Buenos Aires"
destinations = ["PMC", "SCL"]
default_max_price = 250000
default_mistake_fare_below = 130000
enabled = true

[[route_thresholds]]
origin = "AEP"
destination = "SCL"
max_price = 130000
mistake_fare_below = 75000
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(len(config.origins), 1)
            self.assertEqual(config.origins[0].code, "AEP")
            self.assertEqual(config.origins[0].destinations, ("PMC", "SCL"))
            self.assertEqual(len(config.routes), 2)
            routes = {route.destination: route for route in config.routes}
            self.assertEqual(str(routes["PMC"].max_price), "250000")
            self.assertEqual(str(routes["PMC"].mistake_fare_below), "130000")
            self.assertEqual(str(routes["SCL"].max_price), "130000")
            self.assertEqual(str(routes["SCL"].mistake_fare_below), "75000")

    def test_threshold_only_route_is_expanded_for_enabled_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "origins.toml"
            config_path.write_text(
                """
[scanner]
provider = "mock"
database_path = "data/test.sqlite3"

[[origins]]
code = "AEP"
name = "Buenos Aires"
destinations = ["SCL"]
enabled = true

[[route_thresholds]]
origin = "AEP"
destination = "EZE"
max_price = 260000
mistake_fare_below = 140000
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(
                [(route.origin, route.destination) for route in config.routes],
                [("AEP", "SCL"), ("AEP", "EZE")],
            )
            routes = {route.destination: route for route in config.routes}
            self.assertEqual(str(routes["EZE"].max_price), "260000")
            self.assertEqual(str(routes["EZE"].mistake_fare_below), "140000")

    def test_disabled_origins_are_not_expanded_to_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "origins.toml"
            config_path.write_text(
                """
[scanner]
provider = "mock"
database_path = "data/test.sqlite3"

[[origins]]
code = "AEP"
name = "Buenos Aires"
destinations = ["SCL"]
enabled = false

[[origins]]
code = "SCL"
name = "Santiago"
destinations = ["EZE"]
default_max_price = 400000
enabled = true
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(len(config.origins), 2)
            self.assertEqual([(route.origin, route.destination) for route in config.routes], [("SCL", "EZE")])

    def test_loads_multiple_providers_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "routes.toml"
            config_path.write_text(
                """
[scanner]
providers = ["flight_finder_scraper", "skyscanner", "flight_finder_scraper"]
database_path = "data/test.sqlite3"

[[routes]]
origin = "SCL"
destination = "LIM"
""".strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.scanner.provider, "flight_finder_scraper")
            self.assertEqual(config.scanner.provider_names, ("flight_finder_scraper", "skyscanner"))
            self.assertEqual(config.origins[0].code, "SCL")

    def test_env_providers_override_toml_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "routes.toml"
            config_path.write_text(
                """
[scanner]
provider = "mock"
database_path = "data/test.sqlite3"

[[routes]]
origin = "SCL"
destination = "LIM"
""".strip(),
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"FARESNIPE_PROVIDERS": "skyscanner, mock"}, clear=False):
                config = load_config(config_path)

            self.assertEqual(config.scanner.provider_names, ("skyscanner", "mock"))
            self.assertEqual(config.scanner.provider, "skyscanner")


if __name__ == "__main__":
    unittest.main()
