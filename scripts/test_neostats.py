from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm_client import LLMNotConfiguredError, LLMRequestError, prepare_reply


def main() -> int:
    try:
        answer = prepare_reply("Please say hello in one short greeting.", "")
    except LLMNotConfiguredError as error:
        print(f"NeoStats test not run: {error}")
        return 2
    except LLMRequestError as error:
        print(f"NeoStats test failed: {error}")
        return 1

    print("NeoStats response:")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
