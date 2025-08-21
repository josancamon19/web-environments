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