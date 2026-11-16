"""One set of categories for every funder, so portfolios can be compared.

Each field is single-choice: the grant's *primary* theme, strategy and population.
That keeps labels comparable across funders and lets agreement be measured with Cohen's kappa.
Edit the definitions here; the labeling prompt, schema, hand-labeling page and dashboard all read from this file.
"""

THEMES = {
    "Education": "Early childhood, K-12, afterschool, higher education, literacy, teacher training.",
    "Health": "Public health, health care, disease prevention and treatment, vaccines, nutrition, mental health, maternal and child health.",
    "Agriculture & Food Systems": "Farming, smallholder farmers, crops, livestock, food security and food systems.",
    "Economic Opportunity": "Jobs, workforce, small business, financial services and inclusion, poverty reduction, economic development.",
    "Environment & Climate": "Water, freshwater, conservation, energy, climate, pollution, sustainability.",
    "Community Development": "Place-based neighborhood revitalization, housing, local infrastructure, community vitality.",
    "Arts & Culture": "Arts organizations, museums, cultural heritage, performing arts.",
    "Democracy & Civic Life": "Civic engagement, voting, governance, civil society, government accountability, public policy institutions.",
    "Justice & Rights": "Criminal and juvenile justice, human rights, legal aid, racial justice.",
    "Gender Equality": "Women's and girls' empowerment, gender-based violence, women's economic power.",
    "Philanthropy & Nonprofit Sector": "Community foundations, philanthropic networks, nonprofit capacity and sector infrastructure.",
    "Data, Research & Technology": "Research infrastructure, data systems, digital public goods, technology for development, when not specific to another theme.",
    "Media & Public Awareness": "Journalism, media, communications campaigns, public understanding.",
    "Emergency Response": "Disaster relief and recovery, humanitarian response, crisis response (e.g. the Flint water crisis response).",
    "Other": "Fits none of the above.",
}

STRATEGIES = {
    "General Operating Support": "Unrestricted support for the organization's overall work.",
    "Direct Services": "Delivering a program or service directly to people.",
    "Policy & Advocacy": "Influencing laws, policy, regulation or public decisions.",
    "Capacity Building & Networks": "Strengthening organizations, leaders, coalitions or fields.",
    "Research & Evaluation": "Studies, data collection, evaluation, learning.",
    "Product & Technology Development": "Developing or testing a product, tool, vaccine, diagnostic or technology.",
    "Capital & Infrastructure": "Buildings, equipment, endowments, loans or other capital.",
    "Communications & Convening": "Events, conferences, campaigns, media and communications.",
}

POPULATIONS = {
    "General Public / Communities": "Everyone in a place or the public broadly.",
    "Children & Youth": "Children, students, adolescents and young adults.",
    "Women & Girls": "Women and girls specifically.",
    "Low-Income People": "People living in poverty or with low incomes.",
    "Racial & Ethnic Communities": "Specific racial or ethnic communities, including Indigenous peoples.",
    "Farmers & Rural Communities": "Farmers, smallholders and rural residents.",
    "Patients & People with Specific Conditions": "People with or at risk of a specific disease or disability.",
    "Organizations & Institutions": "The intended beneficiaries are organizations, governments or a field, not people directly.",
    "Not Specified": "The record doesn't say who benefits.",
}

FIELDS = {"theme": THEMES, "strategy": STRATEGIES, "population": POPULATIONS}
CONFIDENCE = ["high", "medium", "low"]


def _block(title: str, options: dict) -> str:
    return f"{title}:\n" + "\n".join(f"- {k}: {v}" for k, v in options.items())


LABEL_INSTRUCTIONS = f"""You label philanthropic grants for a portfolio analysis that compares funders.
Read one grant record and choose exactly one value per field: the grant's PRIMARY focus, not every topic it touches.

{_block("theme", THEMES)}

{_block("strategy", STRATEGIES)}

{_block("population", POPULATIONS)}

Rules:
- Judge from the record only: the grantee, where it works, and what the grant is for.
- "General operating support" or "general purposes" wording means strategy General Operating Support.
- confidence: high if the record clearly supports all three labels, medium if one is a judgment call, low if the
  record is too thin to tell.
- rationale: one short sentence naming the words in the record that decided the theme."""

LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "theme": {"type": "string", "enum": list(THEMES)},
        "strategy": {"type": "string", "enum": list(STRATEGIES)},
        "population": {"type": "string", "enum": list(POPULATIONS)},
        "confidence": {"type": "string", "enum": CONFIDENCE},
        "rationale": {"type": "string"},
    },
    "required": ["theme", "strategy", "population", "confidence", "rationale"],
    "additionalProperties": False,
}


def grant_text(g: dict) -> str:
    """The grant as the labeler (model or person) sees it.

    The funder's own program and topic are deliberately left out: they are the benchmark the labels are
    checked against (see benchmark.py), so showing them would let the labeler copy the answer.
    """
    lines = [
        f"Funder: {g['funder']}",
        f"Grantee: {g['grantee']}",
        f"Grantee location: {g['location']}" + (f" | Serves: {g['geographic_focus']}" if g.get("geographic_focus") else ""),
    ]
    if g.get("title") and g["title"].lower() != (g.get("description") or "").lower():
        lines.append(f"Title: {g['title']}")
    lines.append(f"Description: {g['description']}")
    return "\n".join(lines)
