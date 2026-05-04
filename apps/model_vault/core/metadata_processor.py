import os
import html
import re

class MetadataProcessor:
    """
    Cleans up Civitai metadata for display and storage.
    Focuses on Stability Matrix compatible patterns.
    """

    @staticmethod
    def clean_html(raw_html):
        """Removes HTML tags and decodes entities."""
        if not raw_html:
            return ""
        # Remove tags
        clean = re.sub('<.*?>', '', raw_html)
        # Decode entities
        return html.unescape(clean).strip()

    @staticmethod
    def format_triggers(trained_words):
        """Converts trainedWords list to a comma-separated string."""
        if not trained_words:
            return ""
        if isinstance(trained_words, list):
            return ", ".join(trained_words)
        return str(trained_words)

    @staticmethod
    def map_arch_to_stability(base_model):
        """
        Maps Civitai 'baseModel' strings to common categories if needed.
        Currently returns the value as is for maximum granularity.
        """
        return base_model if base_model else "Unknown"
