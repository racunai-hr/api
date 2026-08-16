from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from expenses.admin import ExpenseAdmin
from expenses.models import Expense, ExpenseAttachment, ExpenseCategory
from expenses.tests.partner_helpers import create_supplier_partner
from expenses.validators import validate_pdf_content, validate_pdf_file_size
from tenants.models import Tenant


def _minimal_pdf(name='test.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 minimal', content_type='application/pdf')


def _fake_pdf(name='bad.pdf'):
    return SimpleUploadedFile(name, b'not-a-pdf', content_type='application/pdf')


@override_settings(MEDIA_ROOT='/tmp/racunai_expense_tests_media')
class ExpenseAttachmentModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='testco', name='Test Co')
        cls.other_tenant = Tenant.objects.create(slug='otherco', name='Other Co')
        cls.user = User.objects.create_user(username='tester', password='test')
        cls.category = ExpenseCategory.all_objects.create(
            tenant=cls.tenant,
            name='Telekom',
        )
        cls.supplier = create_supplier_partner(
            tenant=cls.tenant,
            name='HT',
            tax_number='12345678901',
        )
        cls.expense = Expense.all_objects.create(
            tenant=cls.tenant,
            expense_number='T-2026-0099',
            status='approved',
            category=cls.category,
            supplier=cls.supplier,
            amount=Decimal('4.00'),
            tax_amount=Decimal('0.80'),
            currency='EUR',
            expense_date='2026-06-04',
            receipt_number='21822-BB12-1',
            description='Test trošak',
            created_by=cls.user,
        )

    def test_upload_path_contains_tenant_and_expense(self):
        attachment = ExpenseAttachment(
            tenant=self.tenant,
            expense=self.expense,
            uploaded_by=self.user,
            original_filename='racun.pdf',
        )
        attachment.file = _minimal_pdf()
        attachment.save()
        self.assertIn(f'expenses/attachments/{self.tenant.id}/{self.expense.id}/', attachment.file.name)

    def test_rejects_non_pdf_content(self):
        with self.assertRaises(ValidationError):
            validate_pdf_content(_fake_pdf())

    def test_rejects_oversized_pdf(self):
        big = SimpleUploadedFile('big.pdf', b'%PDF-' + (b'0' * (10 * 1024 * 1024)), content_type='application/pdf')
        with self.assertRaises(ValidationError):
            validate_pdf_file_size(big)

    def test_original_filename_auto_filled(self):
        attachment = ExpenseAttachment(
            tenant=self.tenant,
            expense=self.expense,
            uploaded_by=self.user,
            file=_minimal_pdf('Racun_21822.pdf'),
        )
        attachment.save()
        self.assertEqual(attachment.original_filename, 'Racun_21822.pdf')


@override_settings(MEDIA_ROOT='/tmp/racunai_expense_tests_media')
class ExpenseAttachmentViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='testco', name='Test Co')
        cls.other_tenant = Tenant.objects.create(slug='otherco', name='Other Co')
        cls.user = User.objects.create_user(username='viewer', password='test')
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Ops')
        cls.supplier = create_supplier_partner(tenant=cls.tenant, name='Dob', tax_number='11111111111')
        cls.expense = Expense.all_objects.create(
            tenant=cls.tenant,
            expense_number='T-2026-0001',
            status='approved',
            category=cls.category,
            supplier=cls.supplier,
            amount=Decimal('4.00'),
            tax_amount=Decimal('0.80'),
            currency='EUR',
            expense_date='2026-06-04',
            description='Test',
            created_by=cls.user,
        )
        cls.attachment = ExpenseAttachment.all_objects.create(
            tenant=cls.tenant,
            expense=cls.expense,
            uploaded_by=cls.user,
            original_filename='racun.pdf',
            file=_minimal_pdf(),
        )

    def setUp(self):
        self.factory = RequestFactory()

    def test_download_forbidden_wrong_tenant(self):
        self.client.force_login(self.user)
        request = self.factory.get(reverse('expenses:attachment_download', args=[self.attachment.pk]))
        request.user = self.user
        request.tenant = self.other_tenant
        from expenses.views import attachment_download
        response = attachment_download(request, self.attachment.pk)
        self.assertEqual(response.status_code, 403)

    def test_download_ok_matching_tenant(self):
        self.client.force_login(self.user)
        request = self.factory.get(reverse('expenses:attachment_download', args=[self.attachment.pk]))
        request.user = self.user
        request.tenant = self.tenant
        from expenses.views import attachment_download
        response = attachment_download(request, self.attachment.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_download_ok_superuser(self):
        admin = User.objects.create_superuser(username='admin', password='admin', email='a@b.c')
        request = self.factory.get(reverse('expenses:attachment_download', args=[self.attachment.pk]))
        request.user = admin
        request.tenant = self.other_tenant
        from expenses.views import attachment_download
        response = attachment_download(request, self.attachment.pk)
        self.assertEqual(response.status_code, 200)


class ExpenseAdminAttachmentLinksTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(slug='testco', name='Test Co')
        cls.user = User.objects.create_user(username='adminuser', password='test')
        cls.category = ExpenseCategory.all_objects.create(tenant=cls.tenant, name='Telekom')
        cls.supplier = create_supplier_partner(tenant=cls.tenant, name='HT', tax_number='12345678901')
        cls.expense = Expense.all_objects.create(
            tenant=cls.tenant,
            expense_number='T-2026-0001',
            status='approved',
            category=cls.category,
            supplier=cls.supplier,
            amount=Decimal('4.00'),
            tax_amount=Decimal('0.80'),
            currency='EUR',
            expense_date='2026-06-04',
            description='Test',
            created_by=cls.user,
        )

    @override_settings(MEDIA_ROOT='/tmp/racunai_expense_tests_media')
    def test_attachment_links_shows_download_link(self):
        ExpenseAttachment.all_objects.create(
            tenant=self.tenant,
            expense=self.expense,
            uploaded_by=self.user,
            original_filename='Racun_21822.pdf',
            file=_minimal_pdf('Racun_21822.pdf'),
        )
        admin = ExpenseAdmin(Expense, None)
        html = admin.attachment_links(self.expense)
        self.assertIn('Racun_21822.pdf', html)
        self.assertIn(reverse('expenses:attachment_download', args=[self.expense.attachments.first().pk]), html)
