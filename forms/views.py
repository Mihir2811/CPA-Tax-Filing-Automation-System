import base64
import json
import fitz  # PyMuPDF
from openai import OpenAI
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from datetime import datetime
import os
import re
import hashlib
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from organizer_extraction_app import models as organizer_models


from .models import FormProcessingStat

client = OpenAI(api_key=settings.OPENAI_API_KEY)

# Form order for sorting
FORM_ORDER = [
    "W-2", "1099-INT", "1099-B", "1099-DIV", "1099-R", "5498", 
    "SSA-1099", "8949", "1099-NEC", "1099-MISC", "1099-K", 
    "1098", "1098-T", "1098-E", "1099-Q"
]

# ============================================================
# 🧩 Base Class (Shared Logic)
# ============================================================
class BaseFormExtractor:
    def __init__(self, pdf_file, page_number=0):
        self.pdf_file = pdf_file
        self.page_number = page_number

    def pdf_page_to_base64(self, dpi=300):
        """Convert all PDF pages to a list of Base64-encoded PNG images."""
        doc = fitz.open(stream=self.pdf_file.read(), filetype="pdf")
        images_b64 = []
        for page_num in range(doc.page_count):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=dpi)
            img_bytes = pix.tobytes("png")
            images_b64.append(base64.b64encode(img_bytes).decode("utf-8"))
        doc.close()
        return images_b64

    def call_openai(self, prompt, image_b64):
        """Send image + prompt to OpenAI."""
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}
                        ],
                    }
                ],
                max_tokens=600,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"OpenAI API Error: {e}")
            return "{}"

    def common_prompt(self):
        return """
            # CONTEXT
            You are analyzing an IRS tax form image. These forms are used for reporting various types of income, payments, and tax-related information to the Internal Revenue Service.

            # YOUR TASK
            Extract specific information from this tax form and return it in a structured JSON format.

            # CRITICAL INSTRUCTIONS FOR MULTI-FORM DOCUMENTS
            - If this document contains multiple different IRS forms (e.g., both a W-2 and a 1099-INT), return ONLY information about the PRIMARY form that occupies most of the page
            - If multiple copies of the SAME form type are present (e.g., two 1099-INTs), treat as a single form instance
            - If you cannot determine a single primary form, return form_number as "Unknown"
            - Do NOT attempt to extract information from multiple distinct forms in one response

            # FIELDS TO EXTRACT

            1. **form_number**
            - What to extract: The form identifier
            - Where to find: Usually at the top or top-right of the form
            - Examples: 'W-2', '1099-MISC', '1099-R', '1099-Q', '1098', etc.
            - Note: Some forms may show only a description; infer the form number from context

            2. **tax_year**
            - What to extract: The 4-digit calendar year for which this form applies
            - Where to find: Look for text like "For calendar year", "Tax year", or year near the form title. It may be visually aligned but separated by other text (e.g., cautions or boxes).
            - CRITICAL VALIDATION:
                * If digits have spaces (e.g., '20 24', '20 23', '20 25'), combine them
                * DO NOT confuse with revision date like '(Rev. January 2022)' or '(Rev. 1-2022)'
                * The revision date is when the form template was last updated and is usually in parentheses near the form number
                * Extract the CALENDAR YEAR (what year the form is for), NOT the revision date. The calendar year is often bolder or in a dedicated spot in the main content.
                * If the revision is an older year (e.g., 2022) but a newer year (e.g., 2024) appears near 'For calendar year' or in the reporting header, use the newer one.
            - Example distinction: 
                * "For calendar year 20 24" or "2024" aligned next to it → extract 2024
                * "(Rev. January 2022)" → IGNORE this, it's just the form version
                * Specific example: In Form 1098, if '(Rev. January 2022)' is near the top but '2024' is in the year box or header, extract 2024

            3. **payer_name OR employer_name**
            - What to extract: The name of the institution/entity making the payment OR the employer
            - Where to find: 
                * For W-2: Look for "Employer's name" → return as "employer_name"
                * For 1099/1098 forms: Look for "PAYER's name" → return as "payer_name"
            - Smart detection: If the form is W-2, use the key "employer_name", otherwise use "payer_name"
            - Examples: 'XYZ Investment Company', 'ABC Corporation', 'School Name'

            4. **recipient_name OR employee_name**
            - What to extract: The name of the individual/company receiving the payment OR the employee
            - Where to find:
                * For W-2: Look for "Employee's name" → return as "employee_name"
                * For 1099/1098 forms: Look for "RECIPIENT's name" → return as "recipient_name"
            - Smart detection: If the form is W-2, use the key "employee_name", otherwise use "recipient_name"
            - Examples: 'Jane Smith', 'John Doe', 'Smith LLC'

            5. **form_type**
            - What to extract: Descriptive category of the form
            - Smart identification based on form content:
                * W-2 → 'Wages'
                * 1099-INT → 'Interest Income'
                * 1099-DIV → 'Dividends and Distributions'
                * 1099-R → 'Distributions From Pensions, Annuities, Retirement'
                * 1099-MISC → 'Miscellaneous Information'
                * 1099-NEC → 'Nonemployee Compensation'
                * 1099-Q → 'Payments From Qualified Education Programs'
                * 1099-K → 'Payment Card and Third Party Network Transactions'
                * 1098 → 'Mortgage Interest Statement'
                * 1098-T → 'Tuition Statement'
                * 1098-E → 'Student Loan Interest Statement'
                * 5498 → 'IRA Contribution Information'
                * SSA-1099 → 'Social Security Benefits'
                * 8949 → 'Sales and Other Dispositions of Capital Assets'

            # FORM IDENTIFICATION QUICK REFERENCE

            **W-2 indicators:**
            - Shows "Wage and Tax Statement"
            - Has "Employer" and "Employee" labels
            - Shows boxes for wages, federal income tax withheld

            **1099-INT indicators:**
            - Shows "Interest Income"
            - Has "PAYER" and "RECIPIENT" labels

            **1099-DIV indicators:**
            - Shows "Dividends and Distributions"
            - Has dividend-related boxes

            **1099-R indicators:**
            - Shows "Distributions From Pensions, Annuities, Retirement or Profit-Sharing Plans, IRAs, Insurance Contracts"
            - Has distribution codes and amounts

            **1099-MISC indicators:**
            - Shows "Miscellaneous Information"
            - Has various income type boxes

            **1099-NEC indicators:**
            - Shows "Nonemployee Compensation"
            - Focus on contractor payments

            **1099-Q indicators:**
            - Shows "Payments From Qualified Education Programs"
            - Education-related payments

            **1098 indicators:**
            - Shows "Mortgage Interest Statement"
            - Has mortgage interest amounts

            **1098-T indicators:**
            - Shows "Tuition Statement"
            - Educational institution as payer

            # OUTPUT FORMAT RULES

            **IMPORTANT:** Return ONLY a valid JSON object with NO additional text, markdown formatting, or code blocks.
            The response must start with { and end with }

            **For W-2 forms, use this structure:**
            {
            "form_number": "string",
            "tax_year": number,
            "employer_name": "string",
            "employee_name": "string",
            "form_type": "string"
            }

            **For all other forms (1099, 1098, etc.), use this structure:**
            {
            "form_number": "string",
            "tax_year": number,
            "payer_name": "string",
            "recipient_name": "string",
            "form_type": "string"
            }

            # HANDLING MISSING DATA
            - For missing string fields: Use "Not found"
            - For missing numeric fields: Use 0

            # EXAMPLE OUTPUTS

            **Example 1 - W-2 Form:**
            {
            "form_number": "W-2",
            "tax_year": 2024,
            "employer_name": "ABC Corporation",
            "employee_name": "John Doe",
            "form_type": "Wages"
            }

            **Example 2 - 1098 Form:**
            {
            "form_number": "1098",
            "tax_year": 2024,
            "payer_name": "Click n' Close, Inc.",
            "recipient_name": "John Doe",
            "form_type": "Mortgage Interest Statement"
            }
            
            **Example 3 - Multi-form Document:**
            {
            "form_number": "Unknown",
            "tax_year": 0,
            "payer_name": "Not found",
            "recipient_name": "Not found",
            "form_type": "Not found"
            }
            """

    def extract(self):
        raise NotImplementedError("Each subclass must define its own extract() method.")


# ============================================================
# 🧾 All Specific IRS Form Extractors
# ============================================================

class FormW2Extractor(BaseFormExtractor):
    def __init__(self, pdf_file, page_number=0):
        super().__init__(pdf_file, page_number)
        self.form_specific = "W-2"

    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        final_result = {
            "form_number": "Not found",
            "tax_year": 0,
            "employer_name": "Not found",
            "employee_name": "Not found",
            "form_type": "Not found",
        }

        prompt_base = self.common_prompt() + (
            "This is a **Form W-2 (Wages)**. The employer_name should reference the employer, "
            "and employee_name should reference the employee. If only 'Wages' is visible, infer form_number='W-2'."
        )

        for img_b64 in images_b64:
            response = self.call_openai(prompt_base, img_b64)
            try:
                data = json.loads(response)
                for key, value in data.items():
                    if value != "Not found" and value != 0:
                        final_result[key] = value
            except Exception:
                continue

        return json.dumps(final_result)


class Form1099INTExtractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        if not images_b64:
            return json.dumps({"error": "No images found"})
        
        image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
        
        prompt = self.common_prompt() + (
            "This is **Form 1099-INT or 1099-B (Interest Income)**. Extract payer, recipient, interest amounts, and tax year. "
            "If only 'Interest Income' appears, infer form_number='1099-INT'."
        )
        return self.call_openai(prompt, image_b64)


class Form1099DIVExtractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        if not images_b64:
            return json.dumps({"error": "No images found"})
        
        image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
        
        prompt = self.common_prompt() + (
            "This is **Form 1099-DIV (Dividend and Distribution)**. Identify dividends and distribution-related details. "
            "If only 'Dividend and Distribution' appears, infer form_number='1099-DIV'."
        )
        return self.call_openai(prompt, image_b64)


class Form1099RExtractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        final_result = {
            "form_number": "Not found",
            "tax_year": 0,
            "payer_name": "Not found",
            "recipient_name": "Not found",
            "form_type": "Not found",
        }

        prompt_base = self.common_prompt() + (
            "This is **Form 1099-R (Distributions From Pensions, Annuities, Retirement or Profit-Sharing Plans, IRAs, Insurance Contracts)**. "
            "Look for retirement, pension, or IRA distribution keywords. "
            "If only 'IRA Distribution' or 'Distributions From Pensions' appears, infer form_number='1099-R'."
        )

        for img_b64 in images_b64:
            response = self.call_openai(prompt_base, img_b64)
            try:
                data = json.loads(response)
                for key, value in data.items():
                    if value != "Not found" and value != 0:
                        final_result[key] = value
            except Exception:
                continue

        return json.dumps(final_result)


class Form5498Extractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        if not images_b64:
            return json.dumps({"error": "No images found"})
        
        image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
        
        prompt = self.common_prompt() + (
            "This is **Form 5498 (IRA Contributions)**. Identify contributions or account information fields. "
            "If only 'IRA Contributions' appears, infer form_number='Form 5498'."
        )
        return self.call_openai(prompt, image_b64)


class FormSSA1099Extractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        if not images_b64:
            return json.dumps({"error": "No images found"})
        
        image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
        
        prompt = self.common_prompt() + (
            "This is **Form SSA-1099 (Social Security benefits)**. Extract SSA issuer, recipient name, and total benefits."
        )
        return self.call_openai(prompt, image_b64)


class Form8949Extractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        if not images_b64:
            return json.dumps({"error": "No images found"})
        
        image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
        
        prompt = self.common_prompt() + (
            "This is **Form 8949 (Capital gain and loss)**. Identify investment or sales gain/loss details. "
            "If only 'Capital gain and loss' appears, infer form_number='Form 8949'."
        )
        return self.call_openai(prompt, image_b64)


class Form1099NECExtractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        if not images_b64:
            return json.dumps({"error": "No images found"})
        
        image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
        
        prompt = self.common_prompt() + (
            "This is **Form 1099-NEC (Nonemployee Compensation)**. Identify compensation or contractor-related fields."
        )
        return self.call_openai(prompt, image_b64)


class Form1099MISCExtractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        if not images_b64:
            return json.dumps({"error": "No images found"})
        
        image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
        
        prompt = self.common_prompt() + (
            "This is **Form 1099-MISC (Miscellaneous Information)**. Identify payer, recipient, and miscellaneous income fields."
        )
        return self.call_openai(prompt, image_b64)


class Form1099KExtractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        if not images_b64:
            return json.dumps({"error": "No images found"})
        
        image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
        
        prompt = self.common_prompt() + (
            "This is **Form 1099-K (Payment Card and Third-Party Network Transactions)**. Identify transactions or payment card data."
        )
        return self.call_openai(prompt, image_b64)


class Form1098Extractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        if not images_b64:
            return json.dumps({"error": "No images found"})
        
        image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
        
        prompt = self.common_prompt() + (
            "This is **Form 1098 (Mortgage Interest Statement, Real Estate Taxes)**. Identify mortgage interest or property tax details."
        )
        return self.call_openai(prompt, image_b64)


class Form1098TExtractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        if not images_b64:
            return json.dumps({"error": "No images found"})
        
        image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
        
        prompt = self.common_prompt() + (
            "This is **Form 1098-T (Tuition Statement)**. Identify student, school, and tuition payment information."
        )
        return self.call_openai(prompt, image_b64)


class Form1098EExtractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        if not images_b64:
            return json.dumps({"error": "No images found"})
        
        image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
        
        prompt = self.common_prompt() + (
            "This is **Form 1098-E (Student Loan Interest Statement)**. Extract borrower name, interest paid, and lender details."
        )
        return self.call_openai(prompt, image_b64)


class Form1099QExtractor(BaseFormExtractor):
    def extract(self):
        images_b64 = self.pdf_page_to_base64()
        if not images_b64:
            return json.dumps({"error": "No images found"})
        
        image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
        
        prompt = self.common_prompt() + (
            "This is **Form 1099-Q (Payments From Qualified Education Programs)**. Identify education payment details and beneficiary info."
        )
        return self.call_openai(prompt, image_b64)


# ============================================================
# 🧠 Dispatcher: Form Number → Extractor Class
# ============================================================

FORM_CLASS_MAP = {
    "W-2": FormW2Extractor,
    "1099-INT": Form1099INTExtractor,
    "1099-B": Form1099INTExtractor,
    "1099-DIV": Form1099DIVExtractor,
    "1099-R": Form1099RExtractor,
    "5498": Form5498Extractor,
    "SSA-1099": FormSSA1099Extractor,
    "8949": Form8949Extractor,
    "1099-NEC": Form1099NECExtractor,
    "1099-MISC": Form1099MISCExtractor,
    "1099-K": Form1099KExtractor,
    "1098": Form1098Extractor,
    "1098-T": Form1098TExtractor,
    "1098-E": Form1098EExtractor,
    "1099-Q": Form1099QExtractor,
}


SUMMARY_SECTION_FORM_MAP = {
    "wages_and_employment": "W-2",
    "interest_income": "1099-INT",
    "dividend_income": "1099-DIV",
    "brokerage_statement_details": "1099-B",
    "1099b_proceeds": "1099-B",
    "ira_distributions": "1099-R",
    "mortgage_interest": "1098",
    "student_loan_interest": "1098-E",
    "tuition_statement_1098t": "1098-T",
    "qualified_education_1099q": "1099-Q",
    "social_security_benefits": "SSA-1099",
    "nonemployee_compensation": "1099-NEC",
    "miscellaneous_information": "1099-MISC",
    "payment_card_1099k": "1099-K",
    "state_tax_refunds": "1099-G",
    "rental_and_royalty_expenses": "Schedule E",
    "rental_and_royalty_property_equipment": "Schedule E",
    "partnership_income": "Schedule K-1",
    "s_corp_income": "Schedule K-1",
    "estate_trust_income": "Schedule K-1",
    "remic_income": "Schedule K-1",
}


def detect_form_type(pdf_file):
    """Detect form type using AI, checking all pages until a form number is found."""
    extractor = BaseFormExtractor(pdf_file)
    images_b64 = extractor.pdf_page_to_base64()
    
    if not images_b64:
        return "Unknown"
    
    prompt = (
        "Analyze this IRS tax form image and identify ONLY the form number. "
        "CRITICAL INSTRUCTIONS:\n"
        "- If this document contains multiple DIFFERENT IRS forms (e.g., both a W-2 and a 1099-INT), return 'Unknown'\n"
        "- If multiple copies of the SAME form type are present (e.g., two 1099-INTs), return that form number\n"
        "- Look for explicit text like 'Form 1099-R', 'Form W-2', etc., usually at the bottom or top\n"
        "- If the page is blank, has only addresses, or no clear form identifier, return 'Unknown'\n"
        "- If you see keywords like 'Distributions From Pensions' or distribution codes, prioritize that over account types like 'SEP-IRA'\n"
        "- Return ONLY the form number (e.g., '1099-R', 'W-2', '5498') or 'Unknown' without any additional text."
    )
    
    detected_forms = set()
    
    for image_b64 in images_b64:
        form_type = extractor.call_openai(prompt, image_b64).strip()
        if form_type != "Unknown" and form_type in FORM_CLASS_MAP:
            detected_forms.add(form_type)
        elif form_type != "Unknown":
            # Invalid form type detected
            return "Unknown"
    
    # If multiple different forms detected, classify as Unknown
    if len(detected_forms) > 1:
        return "Unknown"
    
    # If exactly one form type detected, return it
    if len(detected_forms) == 1:
        return list(detected_forms)[0]
    
    return "Unknown"


def get_form_extractor(form_number, pdf_file, page_number=0):
    if form_number not in FORM_CLASS_MAP:
        class GenericFormExtractor(BaseFormExtractor):
            def extract(self):
                images_b64 = self.pdf_page_to_base64()
                if not images_b64:
                    return json.dumps({"error": "No images found"})
                
                image_b64 = images_b64[self.page_number] if self.page_number < len(images_b64) else images_b64[0]
                prompt = self.common_prompt()
                return self.call_openai(prompt, image_b64)
        
        return GenericFormExtractor(pdf_file, page_number)
    
    extractor_class = FORM_CLASS_MAP.get(form_number)
    return extractor_class(pdf_file, page_number)


def calculate_file_hash(file_content):
    """Calculate SHA-256 hash of file content."""
    return hashlib.sha256(file_content).hexdigest()


def is_file_already_processed(client_name, file_hash):
    """Check if file hash already exists for this client."""
    return organizer_models.ProcessedFileHash.objects.filter(
        client_name=client_name,
        file_hash=file_hash
    ).exists()


def save_processed_file_hash(client_name, file_hash, file_name):
    """Save file hash to prevent reprocessing."""
    organizer_models.ProcessedFileHash.objects.get_or_create(
        client_name=client_name,
        file_hash=file_hash,
        defaults={'file_name': file_name}
    )


def sanitize_filename(name):
    """Remove invalid characters from filename."""
    if not name:
        return "unknown"
    # Remove or replace invalid characters
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    # Replace multiple spaces with single space
    name = re.sub(r'\s+', ' ', name)
    return name.strip() or "unknown"


def _normalize_client_key(name):
    """Normalize client names for fuzzy matching."""
    if not name:
        return ""
    import re

    normalized = name.replace('_', ' ').replace('-', ' ')
    normalized = re.sub(r'[<>:"/\\|?*]', ' ', normalized)
    normalized = re.sub(r'[^0-9a-zA-Z ]+', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized.strip().lower()


def _get_document_for_client(client_name=None, document_id=None):
    """Locate a TaxDocument for a given client name or explicit id."""
    queryset = organizer_models.TaxDocument.objects.select_related('extracted_data').order_by('-uploaded_at')

    if document_id:
        return queryset.filter(id=document_id).first()

    normalized = _normalize_client_key(client_name)
    if not normalized:
        return None

    # Attempt direct file_name match first
    direct_match = queryset.filter(file_name__icontains=client_name or "").first()
    if direct_match:
        return direct_match

    alt_client_name = (client_name or "").replace(' ', '_')
    alt_match = queryset.filter(file_name__icontains=alt_client_name).first()
    if alt_match:
        return alt_match

    # Fallback: iterate and compare normalized labels
    for document in queryset:
        candidates = [
            document.file_name or "",
            document.get_display_name() or "",
        ]
        extracted = getattr(document, "extracted_data", None)
        if extracted and extracted.summary_data:
            taxpayer = extracted.summary_data.get("taxpayer", "")
            spouse = extracted.summary_data.get("spouse", "")
            candidates.extend([taxpayer or "", spouse or ""])

        for candidate in candidates:
            candidate_key = _normalize_client_key(candidate)
            if candidate_key and (
                candidate_key == normalized or
                candidate_key in normalized or
                normalized in candidate_key
            ):
                return document
    return None


def derive_required_forms(summary_data):
    """Map summary_data sections to expected IRS forms."""
    required_counts = {}
    required_details = {}

    if not isinstance(summary_data, dict):
        return required_counts, required_details

    for section_key, form_number in SUMMARY_SECTION_FORM_MAP.items():
        section_items = summary_data.get(section_key)
        if not isinstance(section_items, list) or not section_items:
            continue

        required_counts[form_number] = required_counts.get(form_number, 0) + len(section_items)
        required_details.setdefault(form_number, []).extend(section_items)

    return required_counts, required_details


def get_client_summary_payload(client_name=None, document_id=None):
    """Return combined summary data + derived requirements for a client."""
    document = _get_document_for_client(client_name, document_id)
    if not document:
        return None

    extracted = getattr(document, "extracted_data", None)
    if not extracted or not extracted.summary_data:
        return None

    forms_required, forms_detail = derive_required_forms(extracted.summary_data)

    return {
        'document_id': document.id,
        'client_display_name': document.get_display_name(),
        'file_name': document.file_name,
        'summary_data': extracted.summary_data,
        'forms_required': forms_required,
        'forms_detail': forms_detail,

        'sorted_forms_count': extracted.sorted_forms_count,
        'unsorted_forms_count': extracted.unsorted_forms_count,
        'last_updated': extracted.extracted_at.isoformat(),
    }


def get_form_order_index(form_number):
    """Get the sort index for a form number."""
    try:
        return FORM_ORDER.index(form_number)
    except ValueError:
        return len(FORM_ORDER)  # Put unknown forms at the end


def create_form_folders(output_dir, file_data):
    """Create organized folder structure for each form type."""
    form_folders = {}
    
    for file_info in file_data:
        form_number = file_info['form_number']
        if form_number not in form_folders:
            folder_name = f"{form_number}_Forms"
            folder_path = os.path.join(output_dir, folder_name)
            os.makedirs(folder_path, exist_ok=True)
            form_folders[form_number] = folder_path
    
    return form_folders


# ============================================================
# 🌐 Django Views
# ============================================================

@require_http_methods(["GET"])
def processing_stats(request):
    """Return the latest sorted vs unsorted totals."""
    stats = FormProcessingStat.get_default()
    return JsonResponse({
        'sorted_count': stats.sorted_count,
        'unsorted_count': stats.unsorted_count,
        'updated_at': stats.updated_at.isoformat()
    })


@require_http_methods(["GET"])
def get_pdf_lists(request, client_name):
    """Get sorted and unsorted PDF lists for a client with unsorted files from model"""
    try:
        # Get document and extracted data
        document = _get_document_for_client(client_name)
        if not document or not hasattr(document, 'extracted_data'):
            return JsonResponse({'success': False, 'error': 'Document not found'})
        
        extracted_data = document.extracted_data
        
        # Get sorted files from folder
        from organizer_extraction_app.utils import create_sorted_folder, list_folder_pdfs, create_unsorted_client_folder
        sorted_folder = create_sorted_folder(client_name)
        sorted_pdfs = list_folder_pdfs(sorted_folder)
        
        # Get unsorted files from folder directly
        unsorted_folder = create_unsorted_client_folder(client_name)
        unsorted_pdfs = list_folder_pdfs(unsorted_folder)
        
        return JsonResponse({
            'success': True,
            'sorted_pdfs': sorted_pdfs,
            'unsorted_pdfs': unsorted_pdfs
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def client_summary(request):
    """Expose summary_data + form requirements for a given client/document."""
    client_name = request.GET.get('client_name')
    document_id = request.GET.get('document_id')

    if not client_name and not document_id:
        return JsonResponse({'error': 'client_name or document_id is required'}, status=400)

    payload = get_client_summary_payload(client_name=client_name, document_id=document_id)
    if not payload:
        return JsonResponse({'error': 'No summary data found for the requested client'}, status=404)

    return JsonResponse(payload)


@csrf_exempt
@require_http_methods(["POST"])
def move_pdf(request):
    """Move PDF between sorted and unsorted folders and update unsorted files list"""
    try:
        data = json.loads(request.body)
        filename = data.get('filename')
        from_folder = data.get('from_folder')
        to_folder = data.get('to_folder')
        taxpayer_name = data.get('taxpayer_name')
        form_type = data.get('form_type')
        
        if not all([filename, from_folder, to_folder, taxpayer_name]):
            return JsonResponse({'success': False, 'error': 'Missing required parameters'})
        
        # Get the document and extracted data
        document = _get_document_for_client(taxpayer_name)
        if not document or not hasattr(document, 'extracted_data'):
            return JsonResponse({'success': False, 'error': 'Document not found'})
        
        extracted_data = document.extracted_data
        
        # Get folder paths
        from organizer_extraction_app.utils import create_sorted_folder, create_unsorted_client_folder
        sorted_folder = create_sorted_folder(taxpayer_name)
        unsorted_folder = create_unsorted_client_folder(taxpayer_name)
        
        if from_folder == 'sorted':
            source_path = os.path.join(sorted_folder, filename)
            dest_folder = unsorted_folder
        else:
            source_path = os.path.join(unsorted_folder, filename)
            dest_folder = sorted_folder
        
        if not os.path.exists(source_path):
            return JsonResponse({'success': False, 'error': 'Source file not found'})
        
        # Generate destination filename
        if to_folder == 'sorted' and form_type:
            safe_form = sanitize_filename(form_type)
            safe_taxpayer = sanitize_filename(taxpayer_name)
            form_index = get_form_order_index(form_type) + 1
            dest_filename = f"{form_type} – {taxpayer_name} – Manual.pdf"
        else:
            dest_filename = filename
        
        dest_path = os.path.join(dest_folder, dest_filename)
        
        # Handle duplicates
        counter = 1
        while os.path.exists(dest_path):
            name, ext = os.path.splitext(dest_filename)
            numbered_filename = f"{name}_{counter:02d}{ext}"
            dest_path = os.path.join(dest_folder, numbered_filename)
            counter += 1
            if counter > 1:
                dest_filename = numbered_filename
        
        # Move the file
        import shutil
        shutil.move(source_path, dest_path)
        
        return JsonResponse({
            'success': True,
            'message': f'Successfully moved {filename} to {to_folder} folder',
            'new_filename': dest_filename
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


def index(request):
    """Render the upload form."""
    return render(request, 'index.html')


@csrf_exempt
def upload_and_extract(request):
    """Handle multiple file uploads and extraction."""
    if request.method == 'POST':
        files = request.FILES.getlist('pdf_files')
        
        if not files:
            return JsonResponse({'error': 'No files uploaded'}, status=400)
        
        # Filter only PDF files
        pdf_files = [f for f in files if f.name.lower().endswith('.pdf')]
        
        if not pdf_files:
            return JsonResponse({'error': 'No PDF files found'}, status=400)
        
        try:
            # Get client name from first processed file
            client_name = "Unknown_Client"
            
            # Create organized directory structure in media
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_dir = os.path.join(settings.MEDIA_ROOT, f'organized_forms_{timestamp}')
            os.makedirs(output_dir, exist_ok=True)
            
            results = []
            file_data = []
            sorted_delta = 0
            unsorted_delta = 0
            skipped_files = []
            
            # Process each PDF file
            for pdf_file in pdf_files:
                try:
                    # Read file content and calculate hash
                    pdf_file.seek(0)
                    file_content = pdf_file.read()
                    file_hash = calculate_file_hash(file_content)
                    
                    # Check if file already processed for this client
                    if is_file_already_processed(client_name, file_hash):
                        skipped_files.append({
                            'filename': pdf_file.name,
                            'reason': 'Already processed (duplicate file)',
                            'hash': file_hash
                        })
                        continue
                    
                    # Detect form type
                    pdf_file.seek(0)
                    detected_form = detect_form_type(pdf_file)
                    
                    # If form is unknown, will be handled later with client-specific unsorted folder
                    if detected_form == "Unknown":
                        # Store for later processing into client-specific unsorted folder
                        file_data.append({
                            'original_name': pdf_file.name,
                            'new_filename': pdf_file.name,
                            'form_number': 'Unknown',
                            'extracted_data': {'form_number': 'Unknown', 'note': 'Form type not identified'},
                            'detected_form': 'Unknown',
                            'sort_index': 999,  # Put unknown forms at end
                            'payer_name': 'Unknown',
                            'recipient_name': 'Unknown',
                            })
                        
                        # Store PDF content and hash for later writing
                        file_data[-1]['pdf_content'] = file_content
                        file_data[-1]['file_hash'] = file_hash
                        continue
                    
                    # Extract form data
                    pdf_file.seek(0)
                    extractor = get_form_extractor(detected_form, pdf_file)
                    extracted_data = extractor.extract()
                    
                    # Parse the extracted data
                    try:
                        extracted_json = json.loads(extracted_data)
                    except json.JSONDecodeError:
                        json_match = re.search(r'```json\s*(\{.*?\})\s*```', extracted_data, re.DOTALL)
                        if json_match:
                            extracted_json = json.loads(json_match.group(1))
                        else:
                            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', extracted_data, re.DOTALL)
                            if json_match:
                                extracted_json = json.loads(json_match.group(0))
                            else:
                                extracted_json = {"raw_response": extracted_data}
                    
                    # Validate extracted form number matches detected form
                    extracted_form_number = extracted_json.get('form_number', 'Unknown')
                    if extracted_form_number != detected_form and extracted_form_number != 'Unknown':
                        # Mismatch between detected and extracted form numbers
                        detected_form = "Unknown"
                        extracted_json = {
                            'form_number': 'Unknown',
                            'tax_year': 0,
                            'payer_name': 'Not found',
                            'recipient_name': 'Not found',
                            'form_type': 'Not found'
                        }
                    
                    # Get payer and recipient names
                    payer_name = extracted_json.get('payer_name') or extracted_json.get('employer_name', 'Unknown')
                    recipient_name = extracted_json.get('recipient_name') or extracted_json.get('employee_name', 'Unknown')
                    form_number = extracted_json.get('form_number', 'Unknown')
                    
                    # Set client name from first valid recipient
                    if client_name == "Unknown_Client" and recipient_name != 'Unknown':
                        client_name = sanitize_filename(recipient_name)
                    
                    # Sanitize names for filename
                    payer_clean = sanitize_filename(payer_name)
                    recipient_clean = sanitize_filename(recipient_name)
                    form_clean = sanitize_filename(form_number)
                    
                    # Create new filename
                    new_filename = f"{payer_clean}({recipient_clean})-{form_clean}.pdf"
                    
                    # Content will be written later to appropriate folder
                    
                    # Store file data for sorting
                    file_data.append({
                        'original_name': pdf_file.name,
                        'new_filename': new_filename,
                        'form_number': form_number,
                        'extracted_data': extracted_json,
                        'detected_form': detected_form,
                        'sort_index': get_form_order_index(form_number),
                        'payer_name': payer_name,
                        'recipient_name': recipient_name,
                    })
                    
                    # Store PDF content and hash for later writing
                    file_data[-1]['pdf_content'] = file_content
                    file_data[-1]['file_hash'] = file_hash
                    
                except Exception as e:
                    results.append({
                        'filename': pdf_file.name,
                        'success': False,
                        'error': str(e)
                    })
                    continue
            
            # Sort files by form order
            file_data.sort(key=lambda x: x['sort_index'])
            
            # Create client-specific sorted and unsorted folders
            from organizer_extraction_app.utils import create_sorted_folder, create_unsorted_client_folder, sanitize_folder_name
            
            sorted_folder = create_sorted_folder(client_name)
            unsorted_folder = create_unsorted_client_folder(client_name)
            
            # Process files into sorted/unsorted folders
            sorted_files = []
            sorted_idx = 1
            for file_info in file_data:
                form_number = file_info['form_number']
                
                # Determine target folder based on form classification
                if form_number != 'Unknown':
                    target_folder = sorted_folder
                    include_in_binder = True
                    # Create standardized filename for sorted folder
                    safe_form = sanitize_folder_name(form_number)
                    safe_client = sanitize_folder_name(client_name)
                    safe_payer = sanitize_folder_name(file_info['payer_name'])
                    sorted_filename = f"{form_number} – {client_name} – {file_info['payer_name']}.pdf"
                    folder_type = 'sorted'
                    sorted_idx += 1
                else:
                    target_folder = unsorted_folder
                    include_in_binder = False
                    # Keep original filename for unsorted folder
                    sorted_filename = file_info['original_name']
                    folder_type = 'unsorted'
                
                new_path = os.path.join(target_folder, sorted_filename)
                
                # Handle duplicates
                counter = 1
                while os.path.exists(new_path):
                    name, ext = os.path.splitext(sorted_filename)
                    numbered_filename = f"{name}_{counter:02d}{ext}"
                    new_path = os.path.join(target_folder, numbered_filename)
                    counter += 1
                    if counter > 1:
                        sorted_filename = numbered_filename
                
                # Write file to target folder
                with open(new_path, 'wb') as f:
                    f.write(file_info['pdf_content'])
                
                # Save file hash to prevent reprocessing
                save_processed_file_hash(client_name, file_info['file_hash'], file_info['original_name'])
                
                file_info['sorted_filename'] = sorted_filename
                file_info['folder_path'] = target_folder
                file_info['include_in_binder'] = include_in_binder
                sorted_files.append(file_info)
                
                results.append({
                    'filename': file_info['original_name'],
                    'new_filename': sorted_filename,
                    'folder': folder_type,
                    'included_in_binder': include_in_binder,
                    'success': True,
                    'detected_form': file_info['detected_form'],
                    'extracted_data': file_info['extracted_data']
                })
                
                if include_in_binder:
                    sorted_delta += 1
                else:
                    unsorted_delta += 1
            
            # Create combined PDF in sorted order (exclude unsorted forms)
            combined_pdf = fitz.open()
            
            for file_info in sorted_files:
                if file_info.get('include_in_binder', True):
                    file_path = os.path.join(file_info['folder_path'], file_info['sorted_filename'])
                    if os.path.exists(file_path):
                        pdf_doc = fitz.open(file_path)
                        combined_pdf.insert_pdf(pdf_doc)
                        pdf_doc.close()
            
            # Save combined PDF with client name in sorted folder
            pdf_filename = f'binder_{client_name}_{timestamp}.pdf'
            pdf_path = os.path.join(sorted_folder, pdf_filename)
            combined_pdf.save(pdf_path)
            combined_pdf.close()


            
            # Keep organized folders in media (don't delete output_dir)
            stats = FormProcessingStat.increment(sorted_delta, unsorted_delta)
            client_summary_info = get_client_summary_payload(client_name=client_name)

            return JsonResponse({
                'success': True,
                'total_files': len(pdf_files),
                'processed_files': len(results),
                'skipped_files': len(skipped_files),
                'results': results,
                'skipped_details': skipped_files,
                'download_url': f'/media/{os.path.basename(sorted_folder)}/{pdf_filename}',
                'sorted_folder': f'/media/{os.path.basename(sorted_folder)}/',
                'unsorted_folder': f'/media/{os.path.basename(unsorted_folder)}/',
                'client_name': client_name,

                'processing_stats': {
                    'sorted_count': stats.sorted_count,
                    'unsorted_count': stats.unsorted_count,
                    'updated_at': stats.updated_at.isoformat()
                },
                'client_summary': client_summary_info
            })
            
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=405)
