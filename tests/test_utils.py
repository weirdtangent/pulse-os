"""Tests for pulse.utils."""

from __future__ import annotations

from pulse.utils import normalize_for_tts


class TestNormalizeForTts:
    """Test TTS text normalization."""

    def test_expands_state_after_comma(self):
        assert normalize_for_tts("Roanoke, VA") == "Roanoke, Virginia"

    def test_expands_state_mid_sentence(self):
        assert normalize_for_tts("In Roanoke, VA today") == "In Roanoke, Virginia today"

    def test_expands_multiple_states(self):
        result = normalize_for_tts("from Roanoke, VA to Austin, TX")
        assert result == "from Roanoke, Virginia to Austin, Texas"

    def test_preserves_non_state_abbreviations(self):
        assert normalize_for_tts("the VA hospital") == "the VA hospital"

    def test_no_expansion_without_comma(self):
        assert normalize_for_tts("VA is a state") == "VA is a state"

    def test_expands_dc(self):
        assert normalize_for_tts("Washington, DC") == "Washington, District of Columbia"

    def test_leaves_plain_text_unchanged(self):
        text = "The weather is sunny and warm"
        assert normalize_for_tts(text) == text

    def test_state_followed_by_punctuation(self):
        assert normalize_for_tts("Roanoke, VA.") == "Roanoke, Virginia."
        assert normalize_for_tts("Roanoke, VA!") == "Roanoke, Virginia!"
