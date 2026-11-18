from datetime import datetime
from django.utils import timezone
from rest_framework import serializers
from expenses_v2.models import Expenses


class ExpensesReadSerializer(serializers.ModelSerializer):
    account_id = serializers.IntegerField(source='account.id', read_only=True)
    account_name = serializers.CharField(source='account.name', read_only=True)
    category_id = serializers.IntegerField(source='category.id', read_only=True, allow_null=True)
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)

    class Meta:
        model = Expenses
        fields = [
            'id',
            'date',
            'expense_date_time',
            'expense',
            'payment_method',
            'type',
            'description',
            'created_at',
            'account_id',
            'account_name',
            'category_id',
            'category_name',
        ]


class ExpensesWriteSerializer(serializers.ModelSerializer):
    date = serializers.DateField(input_formats=['%Y-%m-%d', '%d-%m-%Y'], required=False)
    expense_date_time = serializers.DateTimeField(
        input_formats=[
            '%Y-%m-%d %H:%M:%S',
            '%d-%m-%Y %H:%M:%S',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%SZ',
            '%d-%m-%YT%H:%M:%S',
            '%d-%m-%YT%H:%M:%SZ',
        ],
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Expenses
        fields = '__all__'

    def validate(self, attrs):
        edt = attrs.get('expense_date_time')
        date = attrs.get('date')

        if edt:
            aware_edt = timezone.localtime(edt) if timezone.is_aware(edt) else timezone.make_aware(edt)
            attrs['expense_date_time'] = aware_edt
            attrs['date'] = aware_edt.date()
        elif date:
            now = timezone.localtime(timezone.now())
            attrs['expense_date_time'] = timezone.make_aware(datetime.combine(date, now.time()))
        else:
            raise serializers.ValidationError({'date': 'Expense date is required.'})

        return attrs
