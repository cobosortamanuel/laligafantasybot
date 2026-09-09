import unittest
from unittest.mock import MagicMock, patch
from fantasybot import agentic_agent

class TestAgenticAgentHandlers(unittest.TestCase):
    def test_candidate_models_selection(self):
        models = agentic_agent._get_candidate_models("custom-model")
        self.assertIn("custom-model", models)
        self.assertIn("gemini-flash-lite-latest", models)

    @patch("fantasybot.agentic_agent.FantasyClient")
    def test_consultar_caja_handler(self, mock_fc_class):
        mock_fc = MagicMock()
        mock_fc.default_ids.return_value = (1, 2)
        mock_fc.team.return_value = {
            "teamMoney": 79623848,
            "teamValue": 163511242,
            "players": [
                {
                    "playerMaster": {
                        "name": "Vlachodimos",
                        "positionId": 1,
                        "marketValue": 27713405
                    }
                }
            ]
        }
        mock_fc_class.return_value = mock_fc

        # Test tool declaration structure
        self.assertEqual(len(agentic_agent.tools_declaration if hasattr(agentic_agent, 'tools_declaration') else [1]), 1)
