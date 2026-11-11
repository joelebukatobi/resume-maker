// Work History bullets the model inferred beyond his notes. Bullets records
// them as it places them; Review asks about each one still on the CV until he
// keeps it, rewrites it or takes it off. Per application, in this browser.

const key = (appId) => `resume-maker.inferred.${appId}`;

export function readInferred(appId) {
  try {
    const list = JSON.parse(localStorage.getItem(key(appId)) || "[]");
    return Array.isArray(list) ? list.filter((item) => item && typeof item.text === "string") : [];
  } catch (error) {
    return [];
  }
}

export function writeInferred(appId, list) {
  try { localStorage.setItem(key(appId), JSON.stringify(list)); } catch (error) { /* it just won't ask */ }
}

export function addInferred(appId, items) {
  if (!items.length) return;
  const kept = readInferred(appId).filter((old) => !items.some((item) => item.text === old.text));
  writeInferred(appId, [...kept, ...items]);
}
