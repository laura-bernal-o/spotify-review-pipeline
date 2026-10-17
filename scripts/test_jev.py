"""One-call smoke test for the Jev (TypeSafe) API. Costs a fraction of a cent."""
from dotenv import load_dotenv
from typesafe_sdk import Choice, TypeSafeClient

load_dotenv()

client = TypeSafeClient()
response = client.system_one(
    state="Downloaded songs stop playing when I go offline.",
    questions={
        "topic": Choice(
            instructions="Primary product area the review is about",
            criteria={
                "access": "login/account access",
                "usability": "controls/navigation/ads",
                "playback": "playback failures/crashes/lag",
                "downloads": "offline/saved music",
                "catalog": "content/search/recommendations/lyrics",
                "billing": "price/charges/paywalls/premium entitlement",
                "support": "support contact/response",
                "other": "anything else",
            },
        )
    },
)

print("topic answer:", response.answers["topic"])
print("full response:", response)
