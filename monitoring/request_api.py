"""Deduplicate authenticated identity/directory reads within one request only."""


class ReadNamespace:
    def __init__(self, namespace, methods):
        self.namespace, self.methods, self.cache = namespace, methods, {}

    def __getattr__(self, name):
        method = getattr(self.namespace, name)
        if name not in self.methods:
            return method

        def read(*args, **kwargs):
            key = (name, repr(args), repr(sorted(kwargs.items())))
            if key not in self.cache:
                self.cache[key] = method(*args, **kwargs)
            return self.cache[key]
        return read


class RequestApi:
    """No credentials or authorization evidence are cached across requests.

    Mutations, native job state and membership reconciliation are never cached.
    All actions still authenticate the token and verify native membership anew.
    """
    def __init__(self, api):
        self.raw_api = api
        self.user = ReadNamespace(api.user, {
            'get_my_info', 'get_member_info_by_id', 'get_list', 'get_info_by_login',
        })
        self.role = ReadNamespace(api.role, {'get_list'})

    def __getattr__(self, name):
        return getattr(self.raw_api, name)
