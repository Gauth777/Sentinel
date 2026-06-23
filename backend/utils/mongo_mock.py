import asyncio

class MockCursor:
    def __init__(self, data):
        self.data = data

    async def to_list(self, length=None):
        if length is not None:
            return self.data[:length]
        return self.data

class MockCollection:
    def __init__(self, name, store):
        self.name = name
        self.store = store

    async def find_one(self, filter, projection=None):
        for doc in self.store:
            match = True
            for k, v in filter.items():
                if doc.get(k) != v:
                    match = False
                    break
            if match:
                res = dict(doc)
                if projection and "_id" in projection and projection["_id"] == 0:
                    res.pop("_id", None)
                return res
        return None

    def find(self, filter=None, projection=None):
        filter = filter or {}
        results = []
        for doc in self.store:
            match = True
            for k, v in filter.items():
                if doc.get(k) != v:
                    match = False
                    break
            if match:
                res = dict(doc)
                if projection and "_id" in projection and projection["_id"] == 0:
                    res.pop("_id", None)
                results.append(res)
        return MockCursor(results)

    async def replace_one(self, filter, replacement, upsert=False):
        for i, doc in enumerate(self.store):
            match = True
            for k, v in filter.items():
                if doc.get(k) != v:
                    match = False
                    break
            if match:
                self.store[i] = dict(replacement)
                return
        if upsert:
            self.store.append(dict(replacement))

    async def find_one_and_update(self, filter, update, projection=None, return_document=True):
        doc = await self.find_one(filter)
        if not doc:
            return None
        
        # Simple support for $inc
        if "$inc" in update:
            for k, v in update["$inc"].items():
                doc[k] = int(doc.get(k, 0) or 0) + v
        # Simple support for $set
        if "$set" in update:
            for k, v in update["$set"].items():
                doc[k] = v
        # Save back
        await self.replace_one(filter, doc)
        return doc

    async def count_documents(self, filter):
        res = self.find(filter)
        return len(res.data)

    async def delete_many(self, filter):
        self.store[:] = [doc for doc in self.store if not all(doc.get(k) == v for k, v in filter.items())]

class MockDatabase:
    def __init__(self):
        self.collections = {}

    def __getattr__(self, name):
        if name not in self.collections:
            self.collections[name] = []
        return MockCollection(name, self.collections[name])

class MockSyncCollection:
    def __init__(self, async_coll):
        self.async_coll = async_coll

    def find_one(self, filter, projection=None):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(self.async_coll.find_one(filter, projection))

    def replace_one(self, filter, replacement, upsert=False):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(self.async_coll.replace_one(filter, replacement, upsert))

    def delete_many(self, filter):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(self.async_coll.delete_many(filter))

    def count_documents(self, filter):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(self.async_coll.count_documents(filter))

class MockSyncDatabase:
    def __init__(self, mock_db):
        self.mock_db = mock_db

    def __getattr__(self, name):
        return MockSyncCollection(getattr(self.mock_db, name))

# Global shared mock state so backend server and pytest tests access the same memory
MOCK_DB_STATE = MockDatabase()
MOCK_SYNC_DB_STATE = MockSyncDatabase(MOCK_DB_STATE)
