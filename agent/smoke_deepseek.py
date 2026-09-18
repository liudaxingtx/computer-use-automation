"""Quick smoke test: does DeepSeek v4-pro accept response_format json_object and
return a clean decision dict for a hand-built page observation?"""
from agent import llm

messages = [
    {"role": "system", "content": "You are a computer-use agent. Decide the next action. Respond with ONLY a JSON object: {\"thought\": \"...\", \"action\": {\"kind\": \"click\", \"index\": 1}, \"goal_reached\": false}."},
    {"role": "user", "content": "TASK: search for member 1001\n\nINTERACTIVE ELEMENTS:\n[1] textbox \"\"\n[2] button \"SEARCH\"\n\nReturn your decision as a single JSON object."},
]

d = llm.deepseek_decide(messages)
print("decision:", d)
assert isinstance(d, dict), "expected dict"
assert "action" in d and "kind" in d["action"], "missing action.kind"
print("OK — DeepSeek structured decision works")
