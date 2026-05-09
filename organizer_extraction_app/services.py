import os
import json
import base64
import logging
import re
from difflib import SequenceMatcher
from io import BytesIO
from datetime import datetime
from openai import OpenAI
from pdf2image import convert_from_path
from dotenv import load_dotenv
from django.conf import settings
from django.template.loader import render_to_string
from azure.communication.email import EmailClient
from organizer_extraction_app import models as organizer_models
from organizer_extraction_app import utils as organizer_utils

# Load environment variables
load_dotenv()

# Initialize logger
logger = logging.getLogger(__name__)

client = OpenAI(api_key=settings.OPENAI_API_KEY)

# ============================================================================
# PDF CONVERSION
# ============================================================================

def pdf_page_to_base64(pdf_path, page_number):
    """Convert a specific PDF page to base64 image"""
    images = convert_from_path(
        pdf_path,
        first_page=page_number,
        last_page=page_number,
        dpi=300
    )
    
    buffered = BytesIO()
    images[0].save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

# ============================================================================
# PROMPTS
# ============================================================================

def get_form_identifier_prompt():
    """Get the prompt for identifying which tax forms are on the page"""
    return """
    Identify which tax form(s) appear on this page.

    **Common Tax Forms to Detect:**
    - personal_information
    - dependents_and_wages (W-2)
    - 1099_int (Interest Income)
    - 1099_div (Dividends)
    - 1099_r (IRA Distribution)
    - 1099_misc (Miscellaneous Income)
    - 1099_nec (Nonemployee Compensation)
    - 1099_k (Payment Card Transactions)
    - 1099_g (State Tax Refund)
    - 1099_q (Qualified Education Payments)
    - 1098 (Mortgage Interest)
    - 1098_e (Student Loan Interest)
    - 1098_t (Tuition)
    - brokerage_statement_details
    - partnership_income (K-1)
    - s_corp_income (K-1)
    - estate_trust_income (K-1)
    - remic_income (K-1)
    - itemized_deductions_medical
    - itemized_deductions_taxes
    - rental_and_royalty_expenses
    - rental_and_royalty_property_equipment
    - business_income_and_cost_of_goods_sold
    - business_expenses_and_property_equipment
    - business_vehicle_and_listed_property
    - itemized_deductions_contributions (STOP PAGE)

    **CRITICAL - BROKERAGE FORM VALIDATION**
    - Classify as "brokerage_statement_details" only if actual brokerage data (payer names, account numbers) exist.
    - If you only see placeholders (A–H, TSJ, etc.) with no real data, classify as "1099_b_continuation_page" instead.

    **CRITICAL - TAX YEAR DETECTION**
    - Look for the tax year (e.g., "2024 TAX ORGANIZER", "Tax Year 2023").
    - Return it as a 4-digit integer (e.g., 2024).
    - If no clear year is visible, return null.

    **CRITICAL - STOP PAGE DETECTION**
    - If the page title or heading includes "Itemized Deductions - Contributions" or "Contributions" (main heading),
      set "is_stop_page" to true.

    **RETURN ONLY JSON with this exact structure:**
    {
        "forms_identified": ["form_type1", "form_type2"],
        "can_extract": true/false,
        "is_stop_page": true/false,
        "detected_tax_year": 2024,
        "reason_if_cannot": ""
    }

    **IMPORTANT RULES**
    - If "is_stop_page" is true, set "can_extract" to true.
    - If no readable data exists, set "can_extract" to false and explain why.
    - Use lower_snake_case for all form identifiers.
    """

def get_base_extraction_context(current_year, previous_year, form_types):
    return f"""
Extract ALL data from the following identified form(s): {', '.join(form_types)}

**TAX YEAR CONTEXT:**
- Current Tax Year: {current_year}
- Previous Tax Year: {previous_year}
- Use these years when referring to amounts (e.g., "{current_year} Amount", "{previous_year} Amount")

**EXTRACTION RULES:**
1. Extract EVERY field visible on the form
2. Extract ALL rows from any tables (not just samples)
3. Do not use empty string "" only if field truly doesn't exist on this form
4. Preserve exact values as shown (numbers, dates, names)
5. For SSN/ID fields showing "ON FILE", write exactly "ON FILE"
6. Always extract Account Numbers when visible (look for patterns like XXX1696, XXXXX8376, etc.)
7. For tables with TSJ column, extract TSJ value for each row (T for Taxpayer, S for Spouse, J for Joint)
"""

def get_personal_information_prompt():
    return """
**Personal Information:**
- Extract: First Name, Last Name, DOB, Email Address for Taxpayer and Spouse
- Look for fields like "Email", "E-mail", "Email Address", "Taxpayer Email", etc.
- Extract the complete email address if present on the form
"""


def get_dependents_wages_prompt():
    return """
**Dependents and Wages (W-2):**
- ALL rows with: TS, Employer Name, Taxable Wages, Tax Withheld (Federal, FICA, Medicare, State, Local)
"""

def get_interest_income_prompt(previous_year):
    return f"""
**Interest Income (1099-INT):**
- TSJ, Name of Payer, **Account Number**, Interest Income, U.S. Bonds and Obligations, Code, Tax-Exempt Interest, {previous_year} Amount
"""

def get_brokerage_statement_prompt():
    return """
**Brokerage Statement Details:**
- ONLY extract if page contains ACTUAL brokerage data (payer names, account numbers, amounts)
- Skip pages with only TSJ placeholders (A-H) and no actual data
- Upper Table Fields:
    TSJ, Payer Name (Broker), Account Number, Information Included (X or P)
- Lower Table Fields:
    Interest Income, U.S. Bonds and Obligations, Code, Tax-Exempt Interest,
    Box 1a Total Ordinary Dividends, Box 1b Qualified Dividends,
    Box 2a Total Capital Gain Distribution, U.S. Bond Interest Amount/Percent.
"""

def get_dividend_income_prompt(previous_year):
    return f"""
**Dividend Income (1099-DIV) – {previous_year}**

**CRITICAL RULES:**

1. **TSJ Field**: 
   - Only accept **T**, **S**, or **J** (case-insensitive).
   - If the TSJ column is **blank**, **leave it blank**.
   - **DO NOT** use row labels like A, B, C, D, E... as TSJ values.
   - **DO NOT** invent or auto-fill TSJ codes.

2. **Valid Row Criteria**:
   - A row is valid **only if**:
     - It has a **Name of Payer**
   - **Ignore all placeholder/empty rows** (e.g., rows with only "A", "B", etc.)

3. **Do NOT create entries for rows A through N unless they contain actual dividend data.**

"""

def get_ira_distributions_prompt(current_year, previous_year):
    return f"""
**IRA Distributions (1099-R):**
- Name of Payer, **Account Number**, {current_year} Gross Distributions, Taxable Amount, Federal Tax Withheld, State Tax Withheld, Is Rollover?, {previous_year} Gross Distributions
"""

def get_partnership_income_prompt():
    return """
**Partnership Income (K-1):**
- Fields: TSJ, Entity Name, Employer ID Number, Health Insurance Paid by Entity
"""

def get_s_corp_income_prompt():
    return """
**S Corporation Income (K-1):**
- Fields: TSJ, Entity Name, Employer ID Number, Health Insurance Paid by Entity
"""

def get_estate_trust_income_prompt():
    return """
**Estate and Trust Income (K-1):**
- Fields: TSJ, Entity Name, Employer ID Number
"""

def get_remic_income_prompt():
    return """
**Real Estate Mortgage Investment Conduit (REMIC) Income (K-1):**
- Fields: TSJ, Entity Name, Employer ID Number
"""

def get_mortgage_interest_prompt(current_year, previous_year):
    return f"""
**Mortgage Interest (1098):**
- TSJ, Paid To, **Account Number**, Form 1098 Received (Yes/No will be indicated by X inside the table), {current_year} Amount, {previous_year} Amount
"""

def get_medical_dental_expenses_prompt(current_year, previous_year):
    return f"""
**Medical and Dental Expenses:**
- Description, {current_year} Amount, {previous_year} Amount
- Include ONLY if value exists in either column
"""

def get_other_medical_expenses_prompt(current_year, previous_year):
    return f"""
**Other Medical Expenses:**
- TSJ, Description, {current_year} Amount, {previous_year} Amount
"""

def get_real_estate_taxes_prompt(current_year, previous_year):
    return f"""
**Real Estate Taxes:**
- TSJ, Description, {current_year} Amount, {previous_year} Amount
"""

def get_other_taxes_paid_prompt(current_year, previous_year):
    return f"""
**Other Taxes Paid:**
- TSJ, Description, {current_year} Amount, {previous_year} Amount
"""

def get_state_tax_refunds_prompt():
    return """
**State Tax Refunds (1099-G):**
- TSJ, State, City, Tax Year, State Refund, Local Refund
"""

def get_other_income_prompt(current_year, previous_year):
    return f"""
**Other Income (1099-MISC, 1099-NEC, etc):**
- TSJ, Nature and Source, {current_year} Amount, {previous_year} Amount
"""

def get_social_security_prompt():
    return """
**Social Security Benefits (SSA-1099):**
- TSJ, Benefits Received, Benefits Repaid, Medicare Premiums, Federal Withheld, State Withheld
"""

def get_student_loan_interest_prompt(current_year, previous_year):
    return f"""
**Student Loan Interest (1098-E):**
- TSJ, Nature and Source, {current_year} Amount, {previous_year} Amount
"""

def get_education_expenses_prompt():
    return """
**Education Expenses (1098-T):**
- Student Name, Institution, Tuition and Fees, Scholarships/Grants, Box Numbers
"""

def get_rental_and_royalty_expenses_prompt(current_year, previous_year):
    return f"""
**Rental and Royalty Expenses:**
- Extract ALL rows visible on the page.
- Fields to extract: TSJ, Description, Location of Property, {current_year} Amount, {previous_year} Amount
- Always include "Location of Property" if shown anywhere on the page (even if other fields are blank).
- Include all properties listed, even if one or more numeric fields are missing.
"""

def get_rental_and_royalty_property_equipment_prompt(current_year, previous_year):
    return f"""
**Rental and Royalty Property and Equipment & Depletion:**
- Extract ALL rows related to rental or royalty property details.
- Fields to extract: TSJ, Description, Location of Property, {current_year} Amount, {previous_year} Amount
- Always capture "Location of Property" as it appears (e.g., street, city, state, or parcel ID).
- Include every entry where any numeric or location data exists.
"""

def get_contributions_prompt(current_year, previous_year):
    return f"""
**Itemized Deductions - Contributions:**
- Identify and separate the THREE main categories:
  1. Cash Contributions (100% limit)
  2. Cash Contributions (50% limit)
  3. Noncash Contributions
- For each: TSJ, Organization, {current_year} Amount, {previous_year} Amount, Method of Valuation
"""

def get_business_income_prompt(current_year, previous_year):
    return f"""
**Business Income and Cost of Goods Sold:**
- TSJ, Business Name
- Extract {current_year} Amount and {previous_year} Amount for all applicable fields
"""

def get_business_expenses_prompt(current_year, previous_year):
    return f"""
**Business Expenses and Property & Equipment:**
- TSJ, Business Name
- Extract {current_year} Amount and {previous_year} Amount for all expense categories
"""

def get_business_vehicle_property_prompt(current_year, previous_year):
    return f"""
**Business Vehicle and Other Listed Property:**
- TSJ, Business Name
- Extract {current_year} Amount and {previous_year} Amount for all applicable fields
"""

def get_output_schema_prompt(page_num, form_types):
    business_schemas = get_business_schemas() if any(form in str(form_types).lower() for form in ['business_income', 'business_expenses', 'business_vehicle']) else ''
    return f"""
Return data in this JSON structure (only include sections that have data on this page):
{{
    "page_number": {page_num},
    "forms_on_page": {json.dumps(form_types)},
    "personal_information": {{
        "taxpayer": {{"first_name": "", "last_name": "", "dob": "", "taxpayeremail": ""}},
        "spouse": {{"first_name": "", "last_name": "", "dob": "", "spouseemail": ""}}
    }},
    "dependents_and_wages": [
        {{"ts": "", "employer_name": "", "taxable_wages": "", "tax_withheld": {{"federal": "", "fica_tier1": "", "medicare": "", "state": "", "local": ""}}}}
    ],
    "interest_income": [
        {{"tsj": "", "payer_name": "", "account_number": "", "interest_income": "", "us_bonds_obligations": "", "code": "", "tax_exempt_interest": "", "prior_year_amount": ""}}
    ],
    "brokerage_statement_details": [
        {{"tsj": "", "payer_name": "","account_number": "","information_included": "","interest_income": "","us_bonds_obligations": "","code": "","tax_exempt_interest": "","box_1a_total_ordinary_dividends": "","box_1b_qualified_dividends": "","box_2a_total_capital_gain": "","us_bond_interest_percent": ""}}
    ],
    "dividend_income": [
        {{"tsj": "", "payer_name": "", "account_number": "", "box_1a_ordinary_dividends": "", "box_1b_qualified_dividends": "", "box_2a_capital_gain": "", "us_bond_interest": "", "tax_exempt_interest": "", "prior_year_amount": ""}}
    ],
    "ira_distributions": [
        {{"payer_name": "", "account_number": "", "gross_distributions": "", "taxable_amount": "", "federal_tax_withheld": "", "state_tax_withheld": "", "is_rollover": "", "prior_year_amount": ""}}
    ],
    "partnership_income": [
        {{"tsj": "", "entity_name": "", "employer_id": "", "health_insurance_paid": ""}}
    ],
    "s_corp_income": [
        {{"tsj": "", "entity_name": "", "employer_id": "", "health_insurance_paid": ""}}
    ],
    "estate_trust_income": [
        {{"tsj": "", "entity_name": "", "employer_id": ""}}
    ],
    "remic_income": [
        {{"tsj": "", "entity_name": "", "employer_id": ""}}
    ],
    "mortgage_interest": [
        {{"tsj": "", "paid_to": "", "account_number": "", "form_1098_received": "", "amount_2024": "", "amount_2023": ""}}
    ],
    "medical_dental_expenses": [
        {{"description": "", "amount_2024": "", "amount_2023": ""}}
    ],
    "other_medical_expenses": [
        {{"tsj": "", "description": "", "amount_2024": "", "amount_2023": ""}}
    ],
    "real_estate_taxes": [
        {{"tsj": "", "description": "", "amount_2024": "", "amount_2023": ""}}
    ],
    "other_taxes_paid": [
        {{"tsj": "", "description": "", "amount_2024": "", "amount_2023": ""}}
    ],
    "state_tax_refunds": [
        {{"tsj": "", "state": "", "city": "", "tax_year": "", "state_refund": "", "local_refund": ""}}
    ],
    "other_income": [
        {{"tsj": "", "nature_source": "", "amount_2024": "", "amount_2023": ""}}
    ],
    "social_security_benefits": [
        {{"tsj": "", "benefits_received": "", "benefits_repaid": "", "medicare_premiums": "", "federal_withheld": "", "state_withheld": ""}}
    ],
    "student_loan_interest": [
        {{"tsj": "", "nature_source": "", "amount_2024": "", "amount_2023": ""}}
    ],
    "education_expenses": [
        {{"student_name": "", "institution": "", "tuition_fees": "", "scholarships_grants": "", "box_numbers": {{}}}}
    ],
    "rental_and_royalty_expenses": [
        {{"tsj": "", "description": "", "location_of_property": "", "amount_2024": "", "amount_2023": ""}}
    ],
    "rental_and_royalty_property_equipment_depletion": [
        {{"tsj": "", "description": "", "location_of_property": "", "amount_2024": "", "amount_2023": ""}}
    ],
    "contributions": {{
        "cash_100_percent_limit": [
            {{"tsj": "", "organization_description": "", "amount_2024": "", "amount_2023": ""}}
        ],
        "cash_50_percent_limit": [
            {{"tsj": "", "organization_description": "", "amount_2024": "", "amount_2023": ""}}
        ],
        "noncash_contributions": [
            {{"tsj": "", "organization_description": "", "amount_2024": "", "amount_2023": "", "method_of_valuation": ""}}
        ]
    }}{business_schemas}
}}

**CRITICAL REMINDERS:**
- Extract ALL rows from tables, not just the first row
- Separate Partnership, S-Corp, Estate/Trust, and REMIC K-1 forms into their respective arrays
- Only include array elements where you can see actual data
- Empty arrays are fine if that form type isn't on this page
- For Medical/Dental expenses, only include rows with values in amount_2024 OR amount_2023
- For Rental and Royalty forms: ALWAYS include "location_of_property" when visible
- For Contributions page: Extract ALL organizations and amounts listed
- For Business forms: Extract TSJ and Business Name only
- For Business forms: Handle multiple businesses (extract all business entities found)

"""

# Add business form schemas to the JSON structure
def get_business_schemas():
    return ''',
    "business_income_and_cost_of_goods_sold": [
        {"tsj": "", "business_name": ""}
    ],
    "business_expenses_and_property_equipment": [
        {"tsj": "", "business_name": ""}
    ],
    "business_vehicle_and_listed_property": [
        {"tsj": "", "business_name": ""}
    ]'''

def get_extraction_prompt(form_types, page_num, detected_tax_year=None):
    """Build extraction prompt from relevant sub-prompts"""
    current_year = detected_tax_year if detected_tax_year else datetime.now().year - 1
    previous_year = current_year - 1

    base_prompt = get_base_extraction_context(current_year, previous_year, form_types)
    
    form_prompt_map = {
        'personal information': get_personal_information_prompt,
        'dependents': get_dependents_wages_prompt,
        'w-2': get_dependents_wages_prompt,
        '1099-int': lambda: get_interest_income_prompt(previous_year),
        'interest income': lambda: get_interest_income_prompt(previous_year),
        'brokerage': get_brokerage_statement_prompt,
        '1099-div': lambda: get_dividend_income_prompt(previous_year),
        'dividend': lambda: get_dividend_income_prompt(previous_year),
        '1099-r': lambda: get_ira_distributions_prompt(current_year, previous_year),
        'ira': lambda: get_ira_distributions_prompt(current_year, previous_year),
        'partnership': get_partnership_income_prompt,
        's corporation': get_s_corp_income_prompt,
        's corp': get_s_corp_income_prompt,
        'estate': get_estate_trust_income_prompt,
        'trust': get_estate_trust_income_prompt,
        'remic': get_remic_income_prompt,
        'mortgage': lambda: get_mortgage_interest_prompt(current_year, previous_year),
        '1098': lambda: get_mortgage_interest_prompt(current_year, previous_year),
        'medical': lambda: get_medical_dental_expenses_prompt(current_year, previous_year),
        'dental': lambda: get_medical_dental_expenses_prompt(current_year, previous_year),
        'other medical': lambda: get_other_medical_expenses_prompt(current_year, previous_year),
        'real estate taxes': lambda: get_real_estate_taxes_prompt(current_year, previous_year),
        'other taxes': lambda: get_other_taxes_paid_prompt(current_year, previous_year),
        '1099-g': get_state_tax_refunds_prompt,
        'state tax refund': get_state_tax_refunds_prompt,
        'other income': lambda: get_other_income_prompt(current_year, previous_year),
        'ssa-1099': get_social_security_prompt,
        'social security': get_social_security_prompt,
        '1098-e': lambda: get_student_loan_interest_prompt(current_year, previous_year),
        'student loan': lambda: get_student_loan_interest_prompt(current_year, previous_year),
        '1098-t': get_education_expenses_prompt,
        'education': get_education_expenses_prompt,
        'rental and royalty expenses': lambda: get_rental_and_royalty_expenses_prompt(current_year, previous_year),
        'rental and royalty property': lambda: get_rental_and_royalty_property_equipment_prompt(current_year, previous_year),
        'equipment': lambda: get_rental_and_royalty_property_equipment_prompt(current_year, previous_year),
        'depletion': lambda: get_rental_and_royalty_property_equipment_prompt(current_year, previous_year),
        'contributions': lambda: get_contributions_prompt(current_year, previous_year),
        'business_income_and_cost_of_goods_sold': lambda: get_business_income_prompt(current_year, previous_year),
        'business income': lambda: get_business_income_prompt(current_year, previous_year),
        'cost of goods sold': lambda: get_business_income_prompt(current_year, previous_year),
        'business_expenses_and_property_equipment': lambda: get_business_expenses_prompt(current_year, previous_year),
        'business expenses': lambda: get_business_expenses_prompt(current_year, previous_year),
        'business property': lambda: get_business_expenses_prompt(current_year, previous_year),
        'business_vehicle_and_listed_property': lambda: get_business_vehicle_property_prompt(current_year, previous_year),
        'business vehicle': lambda: get_business_vehicle_property_prompt(current_year, previous_year),
        'listed property': lambda: get_business_vehicle_property_prompt(current_year, previous_year),
    }

    form_prompts = []
    seen_prompts = set()
    
    for form in form_types:
        form_lower = form.lower()
        for key, prompt_func in form_prompt_map.items():
            if key in form_lower:
                if callable(prompt_func):
                    result = prompt_func() if prompt_func.__code__.co_argcount == 0 else prompt_func
                    if result not in seen_prompts:
                        form_prompts.append(result)
                        seen_prompts.add(result)
                break

    footer = get_output_schema_prompt(page_num, form_types)
    return "\n\n".join([base_prompt] + form_prompts + [footer])

# ============================================================================
# DATA CLEANING & JSON PARSING
# ============================================================================

def parse_json_response(content):
    """Extract and parse JSON from GPT response"""
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
    return json.loads(content)

def clean_dividend_rows(dividend_rows):
    """Remove invalid TSJ values (A,B,C…) and keep only rows with real data"""
    if not dividend_rows:
        return []
    
    valid_tsj = {'T', 'S', 'J', 't', 's', 'j'}
    cleaned = []
    
    for row in dividend_rows:
        tsj = str(row.get("tsj", "")).strip()
        payer = str(row.get("payer_name", "")).strip()
        
        # --- TSJ VALIDATION ---
        if tsj:
            if tsj.upper() not in valid_tsj:
                continue  # Skip rows where TSJ is A, B, C, etc.
            row["tsj"] = tsj.upper()
        else:
            row["tsj"] = ""  # Keep blank if truly blank
        
        # --- PAYER VALIDATION ---
        if not payer:
            continue  # Skip rows with no payer name
        
        cleaned.append(row)
    
    return cleaned

def clean_extracted_data(data):
    """Clean and normalize extracted data — remove nulls, trim whitespace, fix types"""
    def _clean(value):
        if isinstance(value, dict):
            cleaned = {k: _clean(v) for k, v in value.items()}
            return {k: v for k, v in cleaned.items() if v not in (None, "", [], {})}
        elif isinstance(value, list):
            cleaned = [_clean(v) for v in value]
            return [v for v in cleaned if v not in (None, "", [], {})]
        elif isinstance(value, str):
            return value.strip()
        return value
    
    cleaned_data = _clean(data)
    
    # === APPLY DIVIDEND-SPECIFIC CLEANING ===
    if isinstance(cleaned_data, dict) and "dividend_income" in cleaned_data:
        cleaned_data["dividend_income"] = clean_dividend_rows(cleaned_data["dividend_income"])
        # Remove the key if no valid rows remain
        if not cleaned_data["dividend_income"]:
            cleaned_data.pop("dividend_income", None)
    
    if isinstance(cleaned_data, dict):
        return {k: v for k, v in cleaned_data.items() if v not in (None, "", [], {})}
    return cleaned_data

# ============================================================================
# GPT VISION API CALLS
# ============================================================================

def call_gpt_vision(prompt, base64_image, max_tokens=4096):
    """Make GPT-4 Vision API call"""
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{base64_image}",
                        "detail": "high"
                    }
                }
            ]
        }],
        max_tokens=max_tokens,
        temperature=0
    )
    return response.choices[0].message.content

# ============================================================================
# FORM IDENTIFICATION
# ============================================================================

def identify_forms(base64_image, page_number):
    """Identify forms on page and check for stop page"""
    logger.info(f"Identifying forms on page {page_number}")
    
    try:
        content = call_gpt_vision(get_form_identifier_prompt(), base64_image, max_tokens=1024)
        form_info = parse_json_response(content)
        
        detected_tax_year = form_info.get("detected_tax_year")
        is_stop_page = form_info.get("is_stop_page", False)
        can_extract = form_info.get("can_extract", False)
        forms_identified = form_info.get("forms_identified", [])
        reason_if_cannot = form_info.get("reason_if_cannot", "")
        
        logger.info(f"Forms detected: {forms_identified}")
        if detected_tax_year:
            logger.info(f"Tax Year: {detected_tax_year}")
        if is_stop_page:
            logger.info(f"STOP PAGE detected ('Itemized Deductions - Contributions')")
        
        return {
            "detected_tax_year": detected_tax_year,
            "is_stop_page": is_stop_page,
            "can_extract": can_extract,
            "forms_identified": forms_identified,
            "reason_if_cannot": reason_if_cannot
        }
        
    except json.JSONDecodeError as e:
        return {
            "error": True,
            "error_type": "json_decode",
            "error_message": str(e),
            "raw_response": content if 'content' in locals() else ""
        }
    except Exception as e:
        return {
            "error": True,
            "error_type": "unexpected",
            "error_message": str(e)
        }

# ============================================================================
# EXTRACTION LOGIC
# ============================================================================

def extract_tax_form_data(pdf_path, page_number):
    """Extract structured tax form data from a specific PDF page"""
    base64_image = pdf_page_to_base64(pdf_path, page_number)
    
    # Step 1: Identify forms
    form_info = identify_forms(base64_image, page_number)
    
    if form_info.get("error"):
        return {
            "page_number": page_number,
            "extraction_skipped": True,
            "is_stop_page": False,
            "reason": form_info.get("error_message", "Failed to identify forms"),
            "forms_identified": []
        }
    
    detected_tax_year = form_info["detected_tax_year"]
    is_stop_page = form_info["is_stop_page"]
    can_extract = form_info["can_extract"]
    forms_identified = form_info["forms_identified"]
    reason_if_cannot = form_info["reason_if_cannot"]
    
    # Skip if cannot extract and not a stop page
    if not can_extract and not is_stop_page:
        return {
            "page_number": page_number,
            "extraction_skipped": True,
            "is_stop_page": False,
            "detected_tax_year": detected_tax_year,
            "reason": reason_if_cannot or "Unextractable page",
            "forms_identified": forms_identified
        }
    
    # Skip continuation pages with no actual data
    if "1099_b_continuation_page" in [f.lower() for f in forms_identified]:
        return {
            "page_number": page_number,
            "extraction_skipped": True,
            "is_stop_page": False,
            "detected_tax_year": detected_tax_year,
            "reason": "Page contains only TSJ placeholders without actual brokerage data",
            "forms_identified": forms_identified
        }
    
    # Step 2: Extract data
    logger.info(f"Extracting data from page {page_number}")
    
    try:
        extraction_prompt = get_extraction_prompt(forms_identified, page_number, detected_tax_year)
        content = call_gpt_vision(extraction_prompt, base64_image)
        data = parse_json_response(content)
        cleaned_data = clean_extracted_data(data)
        cleaned_data["is_stop_page"] = is_stop_page
        
        return cleaned_data
        
    except json.JSONDecodeError as e:
        return {
            "page_number": page_number,
            "extraction_error": True,
            "is_stop_page": is_stop_page,
            "forms_identified": forms_identified,
            "error_message": "Failed to parse extracted JSON",
            "error": str(e),
            "raw_response": content[:500] if 'content' in locals() else ""
        }
    except Exception as e:
        return {
            "page_number": page_number,
            "extraction_error": True,
            "is_stop_page": is_stop_page,
            "forms_identified": forms_identified if 'forms_identified' in locals() else [],
            "error_message": str(e)
        }

def process_multiple_pages(pdf_path, start_page=1, num_pages=1):
    """Iterate through multiple PDF pages and stop automatically at the STOP PAGE"""
    all_data = []
    
    for page_num in range(start_page, start_page + num_pages):
        logger.info(f"Processing page {page_num}...")
        
        try:
            page_data = extract_tax_form_data(pdf_path, page_num)
            all_data.append({"page": page_num, "data": page_data})
            
            if page_data.get("is_stop_page"):
                logger.info(f"STOPPING at page {page_num} (Contributions page detected)")
                break
                
        except Exception as e:
            logger.error(f"Error processing page {page_num}: {e}")
            all_data.append({"page": page_num, "error": str(e)})
    
    return all_data

# ============================================================================
# OUTPUT FORMATTING
# ============================================================================

def format_output_tables(data):
    """Format extracted data into readable tables"""
    output = []
    
    for page_data in data:
        page_num = page_data.get("page", "Unknown")
        extracted = page_data.get("data", {})
        
        output.append(f"\n{'='*80}")
        output.append(f"PAGE {page_num}{' 🛑 STOP PAGE 🛑' if extracted.get('is_stop_page') else ''}")
        output.append(f"{'='*80}")
        
        # Handle skipped pages
        if extracted.get("extraction_skipped"):
            output.append(f"⊘ SKIPPED: {extracted.get('reason', 'Unknown reason')}")
            if extracted.get("forms_identified"):
                output.append(f"  Forms identified: {', '.join(extracted['forms_identified'])}")
            continue
        
        # Handle errors
        if extracted.get("extraction_error"):
            output.append(f"⚠ ERROR: {extracted.get('error_message', 'Unknown error')}")
            if extracted.get("forms_identified"):
                output.append(f"  Forms identified: {', '.join(extracted['forms_identified'])}")
            continue
        
        # Display forms detected
        if "forms_on_page" in extracted:
            output.append(f"\n📄 Forms on this page: {', '.join(extracted['forms_on_page'])}")
        
        # Personal Information Section
        if "personal_information" in extracted:
            taxpayer = extracted["personal_information"].get("taxpayer", {})
            spouse = extracted["personal_information"].get("spouse", {})
            if taxpayer or spouse:
                output.append("\n┌─ PERSONAL INFORMATION")
                if taxpayer.get("first_name") or taxpayer.get("last_name"):
                    output.append(f"│ Taxpayer: {taxpayer.get('first_name', '')} {taxpayer.get('last_name', '')}")
                    if taxpayer.get("dob"):
                        output.append(f"│ DOB: {taxpayer['dob']}")
                if spouse.get("first_name") or spouse.get("last_name"):
                    output.append(f"│ Spouse: {spouse.get('first_name', '')} {spouse.get('last_name', '')}")
                    if spouse.get("dob"):
                        output.append(f"│ DOB: {spouse['dob']}")
                output.append("└" + "─"*79)
        
        # Define metadata keys to skip
        metadata_keys = {
            "personal_information", "page_number", "forms_on_page",
            "is_stop_page", "extraction_error", "extraction_skipped",
            "reason", "forms_identified"
        }
        
        # Dynamic section rendering
        for section, value in extracted.items():
            if section in metadata_keys:
                continue
            
            # Special handling for contributions (nested structure)
            if section == "contributions" and isinstance(value, dict):
                output.append("\n┌─ CONTRIBUTIONS (ITEMIZED DEDUCTIONS) 🛑")
                for subkey, items in value.items():
                    if items:
                        title = subkey.replace("_", " ").upper()
                        output.append(f"│ {title}")
                        for i, item in enumerate(items, 1):
                            output.append(f"│  [{i}] {item.get('organization_description', 'N/A')}")
                            for field, val in item.items():
                                if val and field != "organization_description":
                                    output.append(f"│     {field}: {val}")
                output.append("└" + "─"*79)
                continue
            
            # Skip empty or non-list values
            if not value or not isinstance(value, list):
                continue
            
            # Render standard array sections
            title = section.replace("_", " ").upper()
            output.append(f"\n┌─ {title}")
            
            for i, item in enumerate(value, 1):
                if not isinstance(item, dict):
                    continue
                output.append(f"│ [{i}]")
                for field, val in item.items():
                    if val not in ("", None, [], {}):
                        output.append(f"│     {field}: {val}")
            output.append("└" + "─"*79)
    
    return "\n".join(output)

# ============================================================================
# FILE OPERATIONS
# ============================================================================

def save_results(data, output_file="tax_form_extracted_data.json"):
    """Save extracted data to a JSON file"""
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"Data saved successfully to {output_file}")
        return {"success": True, "file": output_file}
    except Exception as e:
        logger.error(f"Failed to save data: {e}")
        return {"success": False, "error": str(e)}

def print_summary(extracted_data):
    """Print extraction summary"""
    total_pages = len(extracted_data)
    skipped = sum(1 for p in extracted_data if p.get("data", {}).get("extraction_skipped"))
    errors = sum(1 for p in extracted_data if p.get("data", {}).get("extraction_error"))
    stop_page_found = any(p.get("data", {}).get("is_stop_page") for p in extracted_data)
    successful = total_pages - skipped - errors
    
    logger.info("EXTRACTION SUMMARY")
    logger.info(f"Successful: {successful} pages")
    logger.info(f"Skipped: {skipped} pages")
    logger.info(f"Errors: {errors} pages")
    logger.info(f"Total: {total_pages} pages")
    
    if stop_page_found:
        stop_page = next((p for p in extracted_data if p.get("data", {}).get("is_stop_page")), None)
        if stop_page:
            logger.info(f"STOPPED at page {stop_page.get('page')} - 'Itemized Deductions - Contributions' detected")
    
    if successful:
        forms_extracted = []
        for page in extracted_data:
            forms = page.get("data", {}).get("forms_on_page", [])
            forms_extracted.extend(forms)
        if forms_extracted:
            unique_forms = sorted(set(forms_extracted))
            logger.info(f"Forms Extracted: {', '.join(unique_forms)}")
    
    logger.info(f"Report generated at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")

# ============================================================================
# EMAIL SERVICE
# ============================================================================

class TaxEmailService:
    """Service for sending extracted tax form data via Azure Communication Email"""
    
    def __init__(self):
        try:
            self.client = EmailClient.from_connection_string(settings.EMAIL_SERVICE)
            self.sender_address = settings.SENDER_ADDRESS
        except Exception as e:
            raise RuntimeError(f"Failed to initialize EmailClient: {e}")
    
    def send_tax_data_email(self, recipient_email, extracted_data, document):
        """Send formatted tax data via email"""
        try:
            
            # Generate or retrieve summary data
            if hasattr(extracted_data, "summary_data") and extracted_data.summary_data:
                summary_dict = extracted_data.summary_data
            else:
                data = getattr(extracted_data, "data", extracted_data)
                summary_dict = organizer_utils.generate_summary_dict(data)
            
            # Extract taxpayer email from the data
            taxpayer_email = None
            try:
                data = getattr(extracted_data, "data", extracted_data)
                if isinstance(data, list) and data:
                    first_page = data[0]
                    personal_info = first_page.get("data", {}).get("personal_information", {})
                    taxpayer_info = personal_info.get("taxpayer", {})
                    taxpayer_email = taxpayer_info.get("email_address") or taxpayer_info.get("email")
            except Exception:
                pass
            
            # Use taxpayer email if available, otherwise use provided recipient_email
            final_recipient = taxpayer_email if taxpayer_email else recipient_email
            
            # Get structured data for template
            summary_data = organizer_utils.generate_summary_html_from_dict(summary_dict)
            
            # Determine recipient name
            recipient_name = (
                summary_dict.get("taxpayer", "Client")
                if isinstance(summary_dict, dict)
                else "Client"
            )

            tracking = organizer_models.EmailTracking.objects.create(email=final_recipient, document=document)
            tracking_url = f"{settings.SITE_URL}/tax/track-email/{tracking.tracking_id}/"
            tracking_pixel = f'<img src="{tracking_url}" width="1" height="1" style="display:none;" />'

            # Build email context
            context = {
                "subject": f"Tax Form Extraction Report for {getattr(document.file, 'name', 'Uploaded Document')}",
                "recipient_name": recipient_name,
                "document_name": getattr(document.file, 'name', 'N/A'),
                "summary_data": summary_data,
                "tracking_pixel": tracking_pixel,
                "sender_name": "Tax Document Extraction System",
                "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            }
            
            # Render HTML email template
            html_body = render_to_string("email/send_email.html", context)

            # Construct email message with dynamic recipient
            message = {
                "senderAddress": self.sender_address,
                "recipients": {"to": [{"address": final_recipient}]},
                "content": {
                    "subject": context["subject"],
                    "html": html_body
                }
            }
            
            # Send email via Azure
            poller = self.client.begin_send(message)
            result = poller.result()
            
            return {
                "success": True,
                "recipient": final_recipient,
                "document": getattr(document.file, "name", "N/A"),
                "message_id": result.get("id", None),
                "status": result.get("status", "Unknown"),
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "recipient": final_recipient if 'final_recipient' in locals() else recipient_email,
                "document": getattr(document.file, "name", "N/A"),
                "timestamp": datetime.utcnow().isoformat()
            }

# ============================================================================
# REQUIRED FORMS SERVICE
# ============================================================================

def generate_required_forms_json(extracted_data):
    """Generate required forms as JSON and store in ExtractedData - captures every individual instance"""
    try:
        if not extracted_data.summary_data:
            return []
        
        summary = extracted_data.summary_data
        required_forms = []
        
        # W-2 Wages - Each employer is a separate required document
        for wage in summary.get('wages_and_employment', []):
            owner = wage.get('owner', '')
            employer = wage.get('employer_name', '')
            if owner and employer:
                required_forms.append({
                    'form_type': 'W-2 Wages',
                    'owner': owner,
                    'entity': employer,
                    'category': 'Income',
                    'unique_key': f"W-2_{owner}_{employer}"
                })
        
        # Interest Income - Each payer/account is a separate required document
        for interest in summary.get('interest_income', []):
            owner = interest.get('owner', '')
            payer = interest.get('payer_name', '')
            account = interest.get('account_number', '')
            if owner and payer:
                unique_key = f"1099-INT_{owner}_{payer}_{account}" if account else f"1099-INT_{owner}_{payer}"
                required_forms.append({
                    'form_type': 'Interest Income (1099-INT)',
                    'owner': owner,
                    'entity': payer,
                    'account': account,
                    'category': 'Income',
                    'unique_key': unique_key
                })
        
        # Dividend Income - Each payer/account is a separate required document
        for dividend in summary.get('dividend_income', []):
            owner = dividend.get('owner', '')
            payer = dividend.get('payer_name', '')
            account = dividend.get('account_number', '')
            if owner and payer:
                unique_key = f"1099-DIV_{owner}_{payer}_{account}" if account else f"1099-DIV_{owner}_{payer}"
                required_forms.append({
                    'form_type': 'Dividend Income (1099-DIV)',
                    'owner': owner,
                    'entity': payer,
                    'account': account,
                    'category': 'Income',
                    'unique_key': unique_key
                })
        
        # IRA Distributions - Each payer/account is a separate required document
        for ira in summary.get('ira_distributions', []):
            payer = ira.get('payer_name', '')
            account = ira.get('account_number', '')
            if payer:
                unique_key = f"1099-R_{payer}_{account}" if account else f"1099-R_{payer}"
                required_forms.append({
                    'form_type': 'IRA Distributions (1099-R)',
                    'entity': payer,
                    'account': account,
                    'category': 'Income',
                    'unique_key': unique_key
                })
        
        # Brokerage Statements - Each broker/account is a separate required document
        for brokerage in summary.get('brokerage_statement_details', []):
            owner = brokerage.get('owner', '')
            payer = brokerage.get('payer_name', '')
            account = brokerage.get('account_number', '')
            if owner and payer:
                unique_key = f"BROKERAGE_{owner}_{payer}_{account}" if account else f"BROKERAGE_{owner}_{payer}"
                required_forms.append({
                    'form_type': 'Brokerage Statement',
                    'owner': owner,
                    'entity': payer,
                    'account': account,
                    'category': 'Income',
                    'unique_key': unique_key
                })
        
        # Partnership K-1 - Each entity is a separate required document
        for partnership in summary.get('partnership_income', []):
            owner = partnership.get('owner', '')
            entity = partnership.get('entity_name', '')
            ein = partnership.get('employer_id', '')
            if owner and entity:
                unique_key = f"K-1-PARTNERSHIP_{owner}_{entity}_{ein}" if ein else f"K-1-PARTNERSHIP_{owner}_{entity}"
                required_forms.append({
                    'form_type': 'Partnership K-1',
                    'owner': owner,
                    'entity': entity,
                    'ein': ein,
                    'category': 'Income',
                    'unique_key': unique_key
                })
        
        # S-Corp K-1 - Each entity is a separate required document
        for scorp in summary.get('s_corp_income', []):
            owner = scorp.get('owner', '')
            entity = scorp.get('entity_name', '')
            ein = scorp.get('employer_id', '')
            if owner and entity:
                unique_key = f"K-1-SCORP_{owner}_{entity}_{ein}" if ein else f"K-1-SCORP_{owner}_{entity}"
                required_forms.append({
                    'form_type': 'S-Corp K-1',
                    'owner': owner,
                    'entity': entity,
                    'ein': ein,
                    'category': 'Income',
                    'unique_key': unique_key
                })
        
        # Estate/Trust K-1 - Each entity is a separate required document
        for estate in summary.get('estate_trust_income', []):
            owner = estate.get('owner', '')
            entity = estate.get('entity_name', '')
            ein = estate.get('employer_id', '')
            if owner and entity:
                unique_key = f"K-1-ESTATE_{owner}_{entity}_{ein}" if ein else f"K-1-ESTATE_{owner}_{entity}"
                required_forms.append({
                    'form_type': 'Estate/Trust K-1',
                    'owner': owner,
                    'entity': entity,
                    'ein': ein,
                    'category': 'Income',
                    'unique_key': unique_key
                })
        
        # Social Security Benefits - Each owner is a separate required document
        for ss in summary.get('social_security_benefits', []):
            owner = ss.get('owner', '')
            if owner:
                required_forms.append({
                    'form_type': 'Social Security Benefits (SSA-1099)',
                    'owner': owner,
                    'amount': ss.get('benefits_received'),
                    'category': 'Income',
                    'unique_key': f"SSA-1099_{owner}"
                })
        
        # Mortgage Interest - Each lender/account is a separate required document
        for mortgage in summary.get('mortgage_interest', []):
            owner = mortgage.get('owner', '')
            paid_to = mortgage.get('paid_to', '')
            account = mortgage.get('account_number', '')
            if owner and paid_to:
                unique_key = f"1098_{owner}_{paid_to}_{account}" if account else f"1098_{owner}_{paid_to}"
                required_forms.append({
                    'form_type': 'Mortgage Interest (1098)',
                    'owner': owner,
                    'entity': paid_to,
                    'account': account,
                    'category': 'Deductions',
                    'unique_key': unique_key
                })
        
        # Student Loan Interest - Each lender is a separate required document
        for student_loan in summary.get('student_loan_interest', []):
            owner = student_loan.get('owner', '')
            source = student_loan.get('nature_source', '')
            if owner and source:
                required_forms.append({
                    'form_type': 'Student Loan Interest (1098-E)',
                    'owner': owner,
                    'entity': source,
                    'category': 'Deductions',
                    'unique_key': f"1098-E_{owner}_{source}"
                })
        
        # Education Expenses - Each institution is a separate required document
        for education in summary.get('education_expenses', []):
            student = education.get('student_name', '')
            institution = education.get('institution', '')
            if student and institution:
                required_forms.append({
                    'form_type': 'Education Expenses (1098-T)',
                    'owner': student,
                    'entity': institution,
                    'category': 'Deductions',
                    'unique_key': f"1098-T_{student}_{institution}"
                })
        
        # State Tax Refunds - Each state is a separate required document
        for refund in summary.get('state_tax_refunds', []):
            owner = refund.get('owner', '')
            state = refund.get('state', '')
            if owner and state:
                required_forms.append({
                    'form_type': 'State Tax Refund (1099-G)',
                    'owner': owner,
                    'entity': state,
                    'category': 'Income',
                    'unique_key': f"1099-G_{owner}_{state}"
                })
        
        # Other Income (1099-MISC, 1099-NEC, etc.) - Each source is a separate required document
        for other in summary.get('other_income', []):
            owner = other.get('owner', '')
            source = other.get('nature_source', '')
            if owner and source:
                required_forms.append({
                    'form_type': 'Other Income (1099-MISC/NEC)',
                    'owner': owner,
                    'entity': source,
                    'category': 'Income',
                    'unique_key': f"1099-OTHER_{owner}_{source}"
                })
        
        extracted_data.required_forms_json = required_forms
        extracted_data.save(update_fields=['required_forms_json'])
        
        logger.info(f"Generated {len(required_forms)} individual required form instances for document: {extracted_data.document.get_display_name()}")
        return required_forms
        
    except Exception as e:
        logger.error(f"Failed to generate required forms: {e}")
        return []

# ============================================================================
# FORMS NORMALIZATION FOR COMPARISON
# ============================================================================

def normalize_required_forms(required_forms_text):
    """Normalize required forms text into structured format for comparison"""
    import re
    
    if not required_forms_text:
        return []
    
    normalized = []
    lines = required_forms_text.strip().split('\n')
    
    for line in lines:
        line = line.strip().rstrip(',')
        if not line or line in ['Income Documents', 'Deductions & Credits', 'Investments, Retirement & Miscellaneous', '{if any}']:
            continue
            
        # Parse different form types
        if ' – ' in line:
            parts = line.split(' – ')
            if len(parts) >= 2:
                form_type = parts[0].strip()
                owner_info = parts[1].strip() if len(parts) > 1 else ''
                entity_info = parts[2].strip() if len(parts) > 2 else ''
                
                # Extract account/EIN info
                account_match = re.search(r'\(Account ([^)]+)\)', line)
                ein_match = re.search(r'\(EIN ([^)]+)\)', line)
                benefits_match = re.search(r'Benefits: \$([^,]+)', line)
                amount_match = re.search(r'\$([\d,]+)', line)
                
                normalized.append({
                    'form_type': form_type,
                    'owner': owner_info,
                    'entity': entity_info,
                    'account': account_match.group(1) if account_match else None,
                    'ein': ein_match.group(1) if ein_match else None,
                    'amount': benefits_match.group(1) if benefits_match else (amount_match.group(1) if amount_match else None),
                    'raw_text': line
                })
    
    return normalized



def normalize_string(s):
    if not s:
        return ""
    # Remove content in parentheses (e.g., (Taxpayer), (Spouse))
    s = re.sub(r'\([^)]*\)', '', s)
    # Remove punctuation and extra spaces
    s = re.sub(r'[^\w\s]', ' ', s)
    return ' '.join(s.lower().split())

# calculate_similarity function moved to utils.py

def normalize_form_type(form_type):
    if not form_type:
        return "unknown"
    ft = form_type.lower()
    if 'w-2' in ft: return 'w-2'
    if '1099-int' in ft: return '1099-int'
    if '1099-div' in ft: return '1099-div'
    if '1099-r' in ft: return '1099-r'
    if '1099-b' in ft: return '1099-b'
    if '1099-g' in ft: return '1099-g'
    if '1099-misc' in ft: return '1099-misc'
    if '1099-nec' in ft: return '1099-nec'
    if 'ssa-1099' in ft or 'social security' in ft: return 'ssa-1099'
    if '1098' in ft: return '1098'
    if 'k-1' in ft: return 'k-1'
    if '5498' in ft: return '5498'
    if 'brokerage' in ft: return 'brokerage'
    return ft

def get_classified_form_type(classified_form):
    # Try detected_form, then form_number, then infer from filename
    ft = classified_form.get('detected_form')
    if not ft or ft == 'Unknown':
        ft = classified_form.get('form_number')
    if not ft or ft == 'Unknown':
        # Fallback to filename check
        fname = (classified_form.get('sorted_filename') or 
                 classified_form.get('file_name') or 
                 classified_form.get('original_filename') or '')
        if 'W-2' in fname: ft = 'W-2'
        elif '1099-INT' in fname: ft = '1099-INT'
        elif '1099-DIV' in fname: ft = '1099-DIV'
        elif 'SSA-1099' in fname: ft = 'SSA-1099'
        elif '1098' in fname: ft = '1098'
        elif 'K-1' in fname: ft = 'K-1'
    return normalize_form_type(ft)

def compare_forms(extracted_data):
    """Compare required forms vs classified forms for verification - handles individual instances"""
    if not extracted_data:
        return {'error': 'No extracted data provided'}
    
    # Get required forms JSON
    required_forms = extracted_data.required_forms_json or []
    

    
    matched_forms = []
    missing_forms = []
    
    for required in required_forms:
        missing_forms.append(required)
    
    extra_classified = []
    
    # Calculate statistics
    total_required = len(required_forms)
    total_classified = 0
    matched_count = len(matched_forms)
    missing_count = len(missing_forms)
    extra_count = 0
    
    completion_rate = (matched_count / total_required * 100) if total_required > 0 else 0
    
    return {
        'total_required': total_required,
        'total_classified': total_classified,
        'matched': matched_count,
        'missing': missing_count,
        'extra': extra_count,
        'completion_rate': completion_rate,
        'matched_forms': matched_forms,
        'missing_forms': missing_forms,
        'extra_classified': extra_classified,
        'required_forms': required_forms,

        'summary': {
            'individual_instances_required': total_required,
            'individual_instances_found': matched_count,
            'unique_form_types_required': len(set(rf.get('form_type', '') for rf in required_forms)),
            'unique_form_types_found': 0
        }
    }