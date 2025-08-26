from typing import Tuple

def get_task_info_from_user() -> Tuple[str, str]:
    """Get task type and description from user input
    
    Returns:
        Tuple[str, str]: (task_type, task_description)
    """
    print("🎯 TASK SETUP")
    print("="*60)
    print("Please select the type of task:")
    print()
    print("1. Action - Perform an action in the browser")
    print("   (e.g., 'Login to Gmail', 'Post a tweet', 'Fill a form')")
    print()
    print("2. Information Retrieval - Find and extract information")
    print("   (e.g., 'Find the price of iPhone 15', 'Get weather forecast')")
    print()
    
    while True:
        choice = input("📝 Select task type (1 or 2): ").strip()
        
        if choice == "1":
            task_type = "action"
            print("✅ Task type: Action")
            break
        elif choice == "2":
            task_type = "information_retrieval"
            print("✅ Task type: Information Retrieval")
            break
        else:
            print("⚠️  Invalid choice. Please enter 1 or 2.")
    
    print()
    print("Now, please describe the task you will perform:")
    print()
    
    task_description = input("📝 Task description: ").strip()
    
    # Validate input
    if not task_description:
        task_description = "General browsing session"
        print(f"⚠️  No description provided. Using default: '{task_description}'")
    else:
        print(f"✅ Task set: {task_description}")
    
    print("="*60)
    print()
    
    return task_type, task_description


def get_task_answer_from_user() -> str:
    """Get the answer for an information retrieval task
    
    Returns:
        str: The answer to the task
    """
    print()
    print("📊 TASK ANSWER")
    print("="*60)
    print("You've completed an information retrieval task.")
    print("Please provide the answer/information you found:")
    print()
    
    answer = input("📝 Answer: ").strip()
    
    if not answer:
        print("⚠️  No answer provided. Recording as 'No answer provided'")
        answer = "No answer provided"
    else:
        print(f"✅ Answer recorded: {answer}")
    
    print("="*60)
    print()
    
    return answer