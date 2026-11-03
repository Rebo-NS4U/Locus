import unittest
from locus_linux.units import to_mph, to_kph, speed_text


class UnitsTests(unittest.TestCase):
    def test_speed_round_trip(self):
        self.assertAlmostEqual(to_mph(1.609344), 1)
        self.assertAlmostEqual(to_kph(60), 96.56064)
        for speed in (.36, 5.04, 23.4, 360):
            self.assertAlmostEqual(to_kph(to_mph(speed)), speed)

    def test_road_speed_descriptions(self):
        self.assertEqual(speed_text("mapped speed · limit 30 km/h"),
                         "mapped speed · limit 18.6 mph")
        self.assertEqual(speed_text("using a 15 km/h estimate."),
                         "using a 9.3 mph estimate.")
        self.assertEqual(speed_text("manual / global"), "manual / global")
        self.assertEqual(speed_text("0.36–360 km/h"), "0.2–223.7 mph")
