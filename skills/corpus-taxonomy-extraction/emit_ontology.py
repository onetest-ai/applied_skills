#!/usr/bin/env python3
"""Emit an OWL (RDF/XML) ontology from taxonomy_v0.json for Cognee grounding.

Cognee's /v1/ontologies accepts an OWL RDF/XML file (filename must end .owl) and,
referenced via `ontologyKey` at cognify time, constrains entity extraction to this
vocabulary — cutting noise and making the graph consistent with your taxonomy.

Produces owl:Class nodes:
  - intent taxonomy: each L1 class, each L2 as rdfs:subClassOf its L1 (under IntentClass)
  - entity kinds (division/branch/region/system/…): classes under Entity
Instances (specific branch/region values) are intentionally NOT emitted — they are data,
and the class-level vocabulary is what grounds extraction.

Usage: emit_ontology.py --taxonomy taxonomy_v0.json --out primo.owl [--base http://onetest.ai/primo]
"""
import argparse, json, re, sys

def cid(s):
    """A safe rdf:ID from a label."""
    x = re.sub(r"[^A-Za-z0-9]+", "_", str(s).strip()).strip("_")
    if not x: x = "n"
    if x[0].isdigit(): x = "n_" + x
    return x

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="http://onetest.ai/taxonomy")
    a = ap.parse_args()
    if not a.out.endswith(".owl"):
        print("warning: Cognee requires the filename to end with .owl", file=sys.stderr)
    tax = json.load(open(a.taxonomy))
    base = a.base.rstrip("#")

    classes = []   # (id, label, parent_id or None)
    seen = set()
    def add(label, parent=None):
        i = cid(label)
        key = (i, parent)
        if key in seen: return i
        seen.add(key); classes.append((i, label, parent)); return i

    # roots
    add("IntentClass"); add("Entity")

    it = tax.get("intent_taxonomy", {})
    for l1, kids in it.get("tree", {}).items():
        l1id = add(l1, "IntentClass")
        for l2 in kids:
            add(l2, l1id)
    for l1 in it.get("l1", []):
        add(l1, "IntentClass")
    for l2 in it.get("unassigned_l2", []):
        add(l2, "IntentClass")

    # entity kinds → classes under Entity (kind label capitalized)
    for kind in tax.get("entities", {}):
        add(kind[:1].upper() + kind[1:], "Entity")

    # render RDF/XML
    L = ['<?xml version="1.0"?>',
         f'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"',
         '         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"',
         '         xmlns:owl="http://www.w3.org/2002/07/owl#"',
         f'         xml:base="{base}"',
         f'         xmlns="{base}#">',
         f'  <owl:Ontology rdf:about="{base}"/>']
    for i, label, parent in classes:
        L.append(f'  <owl:Class rdf:about="{base}#{i}">')
        L.append(f'    <rdfs:label>{esc(label)}</rdfs:label>')
        if parent:
            L.append(f'    <rdfs:subClassOf rdf:resource="{base}#{parent}"/>')
        L.append('  </owl:Class>')
    L.append('</rdf:RDF>')
    open(a.out, "w").write("\n".join(L) + "\n")
    print(f"wrote {a.out}: {len(classes)} classes ({sum(1 for c in classes if c[2]=='IntentClass' or (c[2] and c[2] not in ('Entity',)))} intent, "
          f"{sum(1 for c in classes if c[2]=='Entity')} entity kinds)", file=sys.stderr)

if __name__ == "__main__":
    main()
