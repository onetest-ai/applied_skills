function normalize(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u2013\u2014]/g, "-")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

module.exports = (output, context) => {
  const haystack = normalize(output);
  const required = String(context.vars.expected_answer_must_contain || "")
    .split("|")
    .map((item) => item.trim())
    .filter(Boolean);
  const forbidden = String(context.vars.expected_answer_must_not_contain || "")
    .split("|")
    .map((item) => item.trim())
    .filter(Boolean);
  const minimum = Number.parseInt(context.vars.min_items, 10) || required.length;
  const requiredResults = required.map((item) => ({
    item,
    found: haystack.includes(normalize(item)),
  }));
  const forbiddenResults = forbidden.map((item) => ({
    item,
    found: haystack.includes(normalize(item)),
  }));
  const found = requiredResults.filter((item) => item.found).length;
  const forbiddenFound = forbiddenResults.filter((item) => item.found);
  const pass = found >= minimum && forbiddenFound.length === 0;
  return {
    pass,
    score: required.length ? found / required.length : 1,
    reason: `${found}/${required.length} required concepts retrieved; ` +
      `${forbiddenFound.length} forbidden concepts retrieved; minimum=${minimum}`,
    componentResults: [
      ...requiredResults.map(({ item, found: itemFound }) => ({
        pass: itemFound,
        score: itemFound ? 1 : 0,
        reason: `${itemFound ? "found" : "missing"}: ${item}`,
      })),
      ...forbiddenResults.map(({ item, found: itemFound }) => ({
        pass: !itemFound,
        score: itemFound ? 0 : 1,
        reason: `${itemFound ? "forbidden concept found" : "forbidden concept absent"}: ${item}`,
      })),
    ],
  };
};
