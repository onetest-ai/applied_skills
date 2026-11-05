import pytest
from taska_lib.sensitive import people_matter

NAMES = ("Nora Quill", "Ravi Tamsin", "Opal Brennick")


@pytest.mark.parametrize("text", [
    "Ravi moves to half time from the 20th; keep Nora at 100% until May.",
    "Extend Nora till the end of the phase at 50%.",
    "Opal's team rolls off effective this week.",
    "He would ramp up to 50% next month.",
    "We may need overtime hours if the integration slips.",
    "Her utilisation is low this sprint.",
    "The hourly rate for the second analyst is lower.",
    "Ravi is allocated at 60% to Harbor.",
    "Plan the allocation for Nora before the kickoff.",
    "We may re-staff them next sprint.",
    "Nora is underperforming on the intake work.",
])
def test_people_matters_are_caught(text):
    assert people_matter(text, names=NAMES)


@pytest.mark.parametrize("text", [
    "Cut delivery exceptions by 50% before the pilot.",
    "Extend the API with a status endpoint.",
    "Release the build on Friday.",
    "The allocation of orders to routes happens the night before.",
    "Staff the help desk script with the new FAQ answers.",
    "Publish the release notes for Harbor on Friday.",
    "Track the rate of failed deliveries per route.",
    "Keep the old portal until May.",
    "Extend Harbor tracking to the second warehouse.",
    "Allocate the test budget across the three phases.",
    "Ramp up the pilot to 50% of routes.",
    "The call centre is staffed from 8 to 6.",
])
def test_ordinary_work_is_not(text):
    assert not people_matter(text)


def test_none_and_empty_are_not():
    assert not people_matter(None, "")


def test_any_text_can_carry_it():
    assert people_matter("Weekly sync", None, "Her utilisation is low.")


@pytest.mark.parametrize("text", [
    "Ravi moves to half time from the 20th; keep Nora at 100% until May.",
    "Extend Nora till the end of the phase at 50%.",
    "Opal's team rolls off effective this week.",
    "Release Nora from the project next week.",
    "Nora is released on Friday.",
    "Ravi is fully allocated.",
    "Nora at 50% from June.",
    "Nora's allocation drops to 50%.",
    "Ravi is on a 3-month extension.",
    "Extend Ravi's contract.",
    "Nora goes full time.",
    "Nora's rate goes up.",
    "Opal is not delivering well.",
    "Nora is 50% allocated to Harbor.",
    "keep nora at 100%",
    "Nora Quill rolled off last week.",
])
def test_people_matters_with_names_are_caught(text):
    assert people_matter(text, names=NAMES)


@pytest.mark.parametrize("text", [
    "Adoption is at 60% of the target.",
    "Migration is at 80%.",
    "Dispatch stays at 90% accuracy.",
    "Move Checkout to 50% of users.",
    "Put Search on 50%.",
    "Keep Harbor at 50%.",
    "Extend Checkout by two weeks.",
    "Extend Harbor to the second warehouse.",
    "Allocation to Phase 2 is approved.",
    "Allocate the test budget across the three phases.",
    "Rolled off the staging pipeline.",
    "The part time-boxed spike ends Friday.",
    "A call-centre staffing forecast is due Monday.",
    "The help desk is staffed by Zendesk.",
    "Update the headcount report.",
    "Release the build to them with themed pages.",
    "Release the build on Friday.",
    "Ramp up the pilot to 50% of routes.",
])
def test_ordinary_work_stays_with_and_without_names(text):
    assert not people_matter(text)
    assert not people_matter(text, names=NAMES)


def test_a_capitalised_word_is_not_a_person_without_names():
    assert not people_matter("Keep Nora at 100% until May.")
    assert people_matter("Keep her at 100% until May.")
    assert people_matter("Keep Nora at 100% until May.", names=("Nora Quill",))


def test_staffing_without_a_name_only_for_our_team():
    assert people_matter("Our staffing plan changes next sprint.")


@pytest.mark.parametrize("text", [
    "Nora stays to the end at 100%.",
    "Nora stays at 100%. She needs to be there to the end as well.",
    "Okay, so Nora, I'll extend till end of November for now, and then we'll see.",
    "And Ravi, remove, I'm not going to extend.",
    "Ravi released at the end of the month",
    "Nora kept (50%) and extended to end of November",
    "Ravi staffed on the US position through the second week of November, then the regional position",
    "Ravi on the US position until mid November, then the lower-rate position",
    "The pilot team is drawn from existing people, no new hires",
])
def test_staffing_said_the_way_meetings_say_it(text):
    assert people_matter(text, names=NAMES)


@pytest.mark.parametrize("text", [
    "Extend the deadline till end of November",
    "The pilot stays to the end of the phase",
    "Keep the old portal until May",
    "Move Checkout to 50% of users",
    "Nora presented the US position on pricing",
    "Ravi will extend the API until the cutover",
])
def test_meeting_phrasing_that_is_still_work(text):
    assert not people_matter(text)
    assert not people_matter(text, names=NAMES)


@pytest.mark.parametrize("text", [
    "Nora stays on the call to the end, 100% sure the demo works.",
    "Nora stays late, and the migration is 100% done.",
    "Ravi stays with the vendor option (60%) on the roadmap.",
    "Nora released at 5pm the hotfix",
    "Ravi, remove the dead code",
    "Opal, please remove the stale flag",
    "Nora on the pricing position until Friday",
    "Ravi on the routing position until Friday",
    "Ravi staffed on call for the release",
    "We will bring additional people to the workshop",
    "Nora released the pricing deck",
    "Ravi's position on routing is unchanged",
    "Nora will stay until the workshop ends to take notes",
    "Ravi will extend the pilot until the cutover",
    "Nora extended the contract review until Monday",
    "Ravi released the build at the end of the sprint",
    "Nora kept the old portal (50% of traffic) running",
])
def test_one_clause_and_a_staffing_object(text):
    assert not people_matter(text)
    assert not people_matter(text, names=NAMES)


def test_nameless_staffing_phrases_need_a_staffing_clause():
    assert people_matter("The pilot team is drawn from existing people, no new hires")
    assert people_matter("We may need to bring additional people.")
    assert not people_matter("We will bring additional people to the workshop")


@pytest.mark.parametrize("text", [
    "extend Nora on the yard work for two more months",
    "We would like to extend Nora on the yard work for two more months.",
    "keep Ravi another six weeks",
])
def test_an_extension_duration_is_a_staffing_object(text):
    assert people_matter(text, names=NAMES)


@pytest.mark.parametrize("text", [
    "extend the deadline for two more weeks",
    "Ravi will extend the API for two more sprints",
    "keep the old portal another six weeks",
])
def test_a_duration_alone_is_still_work(text):
    assert not people_matter(text)
    assert not people_matter(text, names=NAMES)


@pytest.mark.parametrize("text", [
    "Nora stays at 80% test coverage on the module",
    "Nora kept (60%) of the scope",
    "Ravi stays at 95% uptime this quarter",
])
def test_a_percentage_that_is_not_time_is_work(text):
    assert not people_matter(text)
    assert not people_matter(text, names=NAMES)


@pytest.mark.parametrize("text", [
    "Nora stays to the end at 100%.",
    "Nora kept (50%) and extended to end of November",
    "Ravi stays at 50% of his time",
    "keep Nora at 100% until May",
    "Ravi moves to half time from the 20th",
])
def test_a_percentage_of_time_is_staffing(text):
    assert people_matter(text, names=NAMES)
