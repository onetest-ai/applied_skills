import source_key as sk


def test_strips_folder_prefix_and_md_suffix():
    s = "Prior engagement 2021 - 2023__Team_Demo_03.14.2022.pptx.md"
    assert sk.brain_source_to_filename(s) == "Team_Demo_03.14.2022.pptx"
    assert sk.brain_source_folder(s) == "Prior engagement 2021 - 2023"


def test_source_without_folder_prefix():
    assert sk.brain_source_to_filename("Team.xlsx.md") == "Team.xlsx"
    assert sk.brain_source_folder("Team.xlsx.md") == ""


def test_nested_source_keeps_the_whole_folder_path():
    # parse_corpus writes rel.replace(os.sep, "__") + ".md", so every component but the last
    # is a folder. A scope pattern on a nested folder must still see that folder.
    s = "Client Docs__Prior engagement 2021 - 2023__Archive__Old plan.pdf.md"
    assert sk.brain_source_to_filename(s) == "Old plan.pdf"
    assert sk.brain_source_folder(s) == "Client Docs/Prior engagement 2021 - 2023/Archive"


def test_match_key_matches_inventory_side():
    import sharepoint_inventory as si
    assert sk.match_key("Team_Demo_03.14.2022.pptx") == si.normalize("Team_Demo_03.14.2022.pptx")
