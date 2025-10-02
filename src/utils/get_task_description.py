def get_source_from_user() -> str:
    """Get task source from user input"""
    print("📚 TASK SOURCE")
    print("=" * 60)
    print("Select the source/benchmark for this task:")
    print("0. none (custom task)")
    print("1. bearcubs")
    print("2. browsercomp")
    print("3. gaia")
    print("4. webvoyager")
    print("5. webarena")
    print("6. mind2web")
    print("7. mind2web2")
    print("8. real")
    print()

    sources = {
        "0": "none",
        "1": "bearcubs",
        "2": "browsercomp",
        "3": "gaia",
        "4": "webvoyager",
        "5": "webarena",
        "6": "mind2web",
        "7": "mind2web2",
        "8": "real",
    }

    while True:
        choice = input("Select source (0-8) [default: 0]: ").strip()

        # Default to 0 if empty input
        if choice == "":
            choice = "0"

        if choice in sources:
            source = sources[choice]
            print(f"✅ Source set: {source}")
            break
        else:
            print("⚠️  Invalid choice. Please enter 0-8.")

    print("=" * 60)
    print()

    return source


def get_task_type_from_user() -> str:
    """Get task type from user input"""
    print("📋 TASK TYPE")
    print("=" * 60)
    print("Select the type of task you will perform:")
    print("1. action - Perform actions on web pages (click, type, navigate, etc.)")
    print("2. information_retrieval - Extract or search for information from web pages")
    print()

    while True:
        choice = input("Select task type (1 or 2): ").strip()

        if choice == "1":
            task_type = "action"
            print(f"✅ Task type set: {task_type}")
            break
        elif choice == "2":
            task_type = "information_retrieval"
            print(f"✅ Task type set: {task_type}")
            break
        else:
            print("⚠️  Invalid choice. Please enter 1 or 2.")

    print("=" * 60)
    print()

    return task_type


def get_task_description_from_user() -> str:
    """Get task description from user input"""
    print("🎯 TASK DESCRIPTION")
    print("=" * 60)
    print("Please describe the task you will perform in the browser:")
    print(
        "(e.g., 'Login to Gmail and send an email', 'Search for products on Amazon', etc.)"
    )
    print()

    task_description = input("📝 Task description: ").strip()

    # Validate input
    if not task_description:
        task_description = "General browsing session"
        print(f"⚠️  No description provided. Using default: '{task_description}'")
    else:
        print(f"✅ Task set: {task_description}")

    print("=" * 60)
    print()

    return task_description


def get_answer_from_user() -> str:
    """Get answer for information retrieval task from user"""
    print("💡 INFORMATION RETRIEVAL ANSWER")
    print("=" * 60)
    print("Please provide the answer/information you found:")
    print("(Enter the information you retrieved during this task)")
    print()
    print("📝 Answer (press Enter when done):")
    answer = input("> ").strip()

    # Validate input
    if not answer:
        print("⚠️  No answer provided. Saving as empty.")
        answer = ""
    else:
        print(f"✅ Answer recorded: {answer[:50]}{'...' if len(answer) > 50 else ''}")

    print("=" * 60)
    print()

    return answer
