"""Tests for pulse.utils."""

from __future__ import annotations

from pulse.utils import normalize_for_tts


class TestNormalizeForTts:
    """Test TTS text normalization."""

    def test_expands_state_after_comma(self):
        assert normalize_for_tts("Roanoke, VA") == "Roanoke Virginia"

    def test_expands_state_mid_sentence(self):
        assert normalize_for_tts("In Roanoke, VA today") == "In Roanoke Virginia today"

    def test_expands_multiple_states(self):
        result = normalize_for_tts("from Roanoke, VA to Austin, TX")
        assert result == "from Roanoke Virginia to Austin Texas"

    def test_preserves_non_state_abbreviations(self):
        assert normalize_for_tts("the VA hospital") == "the VA hospital"

    def test_no_expansion_without_comma(self):
        assert normalize_for_tts("VA is a state") == "VA is a state"

    def test_expands_dc(self):
        assert normalize_for_tts("Washington, DC") == "Washington District of Columbia"

    def test_leaves_plain_text_unchanged(self):
        text = "The weather is sunny and warm"
        assert normalize_for_tts(text) == text

    def test_state_followed_by_punctuation(self):
        assert normalize_for_tts("Roanoke, VA.") == "Roanoke Virginia."
        assert normalize_for_tts("Roanoke, VA!") == "Roanoke Virginia!"

    def test_drops_comma_before_spelled_out_state(self):
        assert normalize_for_tts("In Roanoke, Virginia, it is 52 degrees.") == "In Roanoke Virginia, it is 52 degrees."

    def test_drops_comma_for_multi_word_city_and_state(self):
        assert normalize_for_tts("Winston-Salem, North Carolina") == "Winston-Salem North Carolina"
        assert normalize_for_tts("St. Louis, MO") == "St. Louis Missouri"

    def test_keeps_comma_after_lowercase_word(self):
        assert normalize_for_tts("If you visit, Virginia is lovely") == "If you visit, Virginia is lovely"

    def test_keeps_comma_before_non_state(self):
        assert normalize_for_tts("Roanoke, Salem, and Vinton") == "Roanoke, Salem, and Vinton"

    def test_state_name_prefix_is_not_a_state(self):
        assert normalize_for_tts("Paris, Texarkana") == "Paris, Texarkana"
