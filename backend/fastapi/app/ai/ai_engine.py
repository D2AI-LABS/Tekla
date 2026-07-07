def interpret_query(query: str):

    prompt = f"""
Convert this query into structured filter rules.

Return ONLY JSON like this:

{{
  "element": "beam",
  "conditions": {{
    "direction": "horizontal",
    "length_greater_than": 2000
  }}
}}

Query:
{query}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    print(interpret_query(
    "create a HEA200 beam from (0,0,0) to (5000,0,0)"
))

    return response.choices[0].message.content