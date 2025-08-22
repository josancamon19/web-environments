class ActualPage:
    _instance = None
    _initialized = False

    def __new__(cls, page=None):
        if cls._instance is None:
            cls._instance = super(ActualPage, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            ActualPage._initialized = True

    def get_page(self):
        return self.page
    
    def set_page(self, page):
        self.page = page

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            raise RuntimeError("ActualPage has not been initialized yet")
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None
        cls._initialized = False