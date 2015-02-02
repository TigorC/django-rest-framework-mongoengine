from datetime import datetime
from unittest import TestCase
import mongoengine as me
from mongoengine import connect
from mongoengine.connection import get_db

from rest_framework import status
from rest_framework import serializers as s
from rest_framework.test import APIRequestFactory
from rest_framework_mongoengine.serializers import DocumentSerializer
from rest_framework_mongoengine.viewsets import ModelViewSet


factory = APIRequestFactory()


# TestCase from mongoengine 0.9.DEV
class MongoTestCase(TestCase):
    """
    TestCase class that clear the collection between the tests
    """

    @property
    def db_name(self):
        from django.conf import settings
        return 'test_%s' % getattr(settings, 'MONGO_DATABASE_NAME', 'dummy')

    def __init__(self, methodName='runtest'):
        connect(self.db_name)
        self.db = get_db()
        super(MongoTestCase, self).__init__(methodName)

    def dropCollections(self):
        for collection in self.db.collection_names():
            if collection == 'system.indexes':
                continue
            self.db.drop_collection(collection)

    def tearDown(self):
        self.dropCollections()


class Job(me.Document):
    title = me.StringField()
    status = me.StringField(choices=('draft', 'published'))
    notes = me.StringField(required=False)
    on = me.DateTimeField(default=datetime.utcnow)
    weight = me.IntField(default=0)


class JobSerializer(DocumentSerializer):
    id = s.Field()
    title = s.CharField()
    status = s.ChoiceField(read_only=True, choices=('draft', 'published'))
    sort_weight = s.IntegerField(source='weight')

    class Meta:
        model = Job
        fields = ('id', 'title', 'status', 'sort_weight')


class TestReadonlyRestore(MongoTestCase):
    def test_restore_object(self):
        job = Job.objects.create(
            title='original title', status='draft', notes='secure')
        data = {
            'title': 'updated title ...',
            'status': 'published',  # this one is read only
            'notes': 'hacked',  # this field should not update
            'sort_weight': 10  # mapped to a field with differet name
        }

        serializer = JobSerializer(job, data=data, partial=True)
        self.assertTrue(serializer.is_valid())
        serializer.save()
        obj = serializer.instance
        self.assertEqual(data['title'], obj.title)
        self.assertEqual('draft', obj.status)
        self.assertEqual('secure', obj.notes)

        self.assertEqual(10, obj.weight)


class Location(me.EmbeddedDocument):
    city = me.StringField()


# list of
class Category(me.EmbeddedDocument):
    id = me.StringField()
    counter = me.IntField(default=0, required=True)


class Secret(me.EmbeddedDocument):
    key = me.StringField()


class SomeObject(me.Document):
    name = me.StringField()
    loc = me.EmbeddedDocumentField('Location')
    categories = me.ListField(me.EmbeddedDocumentField(Category))
    codes = me.ListField(me.EmbeddedDocumentField(Secret))


class LocationSerializer(DocumentSerializer):
    class Meta:
        model = Location


class CategorySerializer(DocumentSerializer):
    id = s.CharField(max_length=24)

    class Meta:
        model = Category
        fields = ('id',)


class SomeObjectSerializer(DocumentSerializer):
    location = LocationSerializer(source='loc')
    categories = CategorySerializer(many=True)

    class Meta:
        model = SomeObject
        fields = ('id', 'name', 'location', 'categories')

    def update(self, instance, validated_data):
        instance.name = validated_data.get('name', instance.name)
        instance.loc = Location(**validated_data.get('loc')) or instance.loc
        data_categories = [
            Category(**c) for c in validated_data.get('categories', [])]
        instance.categories = data_categories or instance.categories
        instance.save()
        return instance

    def create(self, validated_data):
        return SomeObject.objects.create(**validated_data)


class SomeObjectViewSet(ModelViewSet):
    model = SomeObject
    queryset = SomeObject.objects.all()
    serializer_class = SomeObjectSerializer


class TestRestoreEmbedded(TestCase):
    def setUp(self):
        self.data = {
            'name': 'some anme',
            'location': {
                'city': 'Toronto'
            },
            'categories': [
                {'id': 'cat1'},
                {'id': 'category_2', 'counter': 666}],
            'codes': [{'key': 'mykey1'}]
        }

    def test_restore_new(self):
        serializer = SomeObjectSerializer(data=self.data)
        self.assertTrue(serializer.is_valid())
        serializer.save()
        obj = serializer.instance

        self.assertEqual(self.data['name'], obj.name)
        self.assertEqual('Toronto', obj.loc.city)

        self.assertEqual(2, len(obj.categories))
        self.assertEqual('category_2', obj.categories[1].id)
        # counter is not listed in serializer fields, cannot be updated
        self.assertEqual(0, obj.categories[1].counter)

        # codes are not listed, should not be updatable
        self.assertEqual(0, len(obj.codes))

    def test_restore_update(self):
        data = self.data
        instance = SomeObject.objects.create(
            name='original',
            loc=Location(city="New York"),
            categories=[Category(id='orig1', counter=777)],
            codes=[Secret(key='confidential123')]
        )
        serializer = SomeObjectSerializer(instance, data=data, partial=True)

        self.assertTrue(serializer.is_valid())
        serializer.save()
        obj = serializer.instance

        self.assertEqual(data['name'], obj.name)
        self.assertEqual('Toronto', obj.loc.city)

        # codes is not listed, should not be updatable
        self.assertEqual(1, len(obj.codes[0]))
        self.assertEqual(
            'confidential123', obj.codes[0].key)  # should keep original val

        self.assertEqual(2, len(obj.categories))
        self.assertEqual('category_2', obj.categories[1].id)
        self.assertEqual(0, obj.categories[1].counter)


class TestModelViewSet(TestCase):
    def setUp(self):
        self.viewset = SomeObjectViewSet.as_view(actions={
            'post': 'create',
            'put': 'update'
        })

    def test_embeded_update(self):
        form_data = {'name': 'test'}
        request = factory.post('/', form_data)
        response = self.viewset(request)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        form_data = response.data
        form_data['name'] = 'test2'
        request = factory.put('/', form_data)
        response = self.viewset(request, id=form_data['id'])
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], form_data['name'])
