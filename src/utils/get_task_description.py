def get_task_type_from_user() -> str:
    """Get task type from user input"""
    print("📋 TASK TYPE")
    print("="*60)
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
    
    print("="*60)
    print()
    
    return task_type


def get_task_description_from_user() -> str:
    """Get task description from user input"""
    print("🎯 TASK DESCRIPTION")
    print("="*60)
    print("Please describe the task you will perform in the browser:")
    print("(e.g., 'Login to Gmail and send an email', 'Search for products on Amazon', etc.)")
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
    
    return task_description