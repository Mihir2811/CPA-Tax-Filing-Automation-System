from datetime import datetime
from typing import Dict, List, Any
from organizer_extraction_app import models as organizer_models
import boto3
from django.conf import settings
from django.utils import timezone
import tempfile
import os
import hashlib
import glob
import re
from django.conf import settings
from difflib import SequenceMatcher

# -----------------
# Data Reshaper
# -----------------

def reshape_to_uniform_sections(extracted_data):
    """Reshape JSON data into uniform list of sections per page"""
    reshaped_pages = []
    
    # Detect tax years from data
    current_year, previous_year = detect_tax_years(extracted_data)
    
    for page_data in extracted_data:
        page_num = page_data.get('page')
        data = page_data.get('data', {})
        
        # Skip error/skipped pages
        if data.get('extraction_skipped') or data.get('extraction_error'):
            continue
            
        sections = []
        
        # Process each data section
        for section_key, section_data in data.items():
            if section_key in ['page_number', 'forms_on_page', 'is_stop_page']:
                continue
                
            if not section_data:
                continue
                
            section = {
                'title': format_section_title(section_key),
                'key': section_key,
                'items': normalize_section_data(section_data, current_year, previous_year)
            }
            
            if section['items']:
                sections.append(section)
        
        if sections:
            reshaped_pages.append({
                'page': page_num,
                'sections': sections
            })
    
    return reshaped_pages

def detect_tax_years(extracted_data):
    """Detect current and previous tax years from extracted data"""
    for page_data in extracted_data:
        data = page_data.get('data', {})
        if data.get('detected_tax_year'):
            current_year = data['detected_tax_year']
            return current_year, current_year - 1
    return 2024, 2023

def format_section_title(key):
    """Convert section key to readable title"""
    title_map = {
        'personal_information': 'Personal Information',
        'dependents_and_wages': 'Dependents & Wages',
        'interest_income': 'Interest Income (1099-INT)',
        'dividend_income': 'Dividend Income (1099-DIV)',
        'brokerage_statement_details': 'Brokerage Statement Details',
        'ira_distributions': 'IRA Distributions (1099-R)',
        'other_income': 'Other Income',
        'rental_and_royalty_income': 'Rental and Royalty Income',
        'rental_and_royalty_expenses': 'Rental and Royalty Expenses',
        'rental_and_royalty_property_equipment_depletion': 'Rental Property & Equipment',
        'partnership_income': 'Partnership Income (K-1)',
        's_corp_income': 'S Corporation Income (K-1)',
        'estate_trust_income': 'Estate and Trust Income (K-1)',
        'remic_income': 'REMIC Income (K-1)',
        'social_security_benefits': 'Social Security Benefits (SSA-1099)',
        'real_estate_taxes': 'Real Estate Taxes',
        'mortgage_interest': 'Mortgage Interest (Form 1098)',
        'medical_dental_expenses': 'Medical and Dental Expenses',
        'other_medical_expenses': 'Other Medical Expenses',
        'other_taxes_paid': 'Other Taxes Paid',
        'investment_interest_expense': 'Investment Interest Expense',
        'state_tax_refunds': 'State Tax Refunds (1099-G)',
        'student_loan_interest': 'Student Loan Interest (1098-E)',
        'education_expenses': 'Education Expenses (1098-T)',
        'business_income_and_cost_of_goods_sold': 'Business Income & Cost of Goods Sold',
        'business_expenses_and_property_equipment': 'Business Expenses & Property Equipment',
        'business_vehicle_and_listed_property': 'Business Vehicle & Listed Property',
        'contributions': 'Charitable Contributions'
    }
    return title_map.get(key, key.replace('_', ' ').title())

def normalize_section_data(data, current_year=2024, previous_year=2023):
    """Convert section data to uniform key-value pairs"""
    if isinstance(data, dict):
        if 'taxpayer' in data or 'spouse' in data:  # Personal info
            items = []
            if data.get('taxpayer'):
                items.append(flatten_dict(data['taxpayer'], 'Taxpayer', current_year, previous_year))
            if data.get('spouse'):
                items.append(flatten_dict(data['spouse'], 'Spouse', current_year, previous_year))
            return items
        elif any(key in data for key in ['cash_100_percent_limit', 'cash_50_percent_limit', 'noncash_contributions']):  # Contributions
            items = []
            for contrib_type, contrib_data in data.items():
                if contrib_data:
                    for item in contrib_data:
                        flattened = flatten_dict(item, '', current_year, previous_year)
                        flattened['contribution_type'] = format_contribution_type(contrib_type)
                        items.append(flattened)
            return items
        else:
            return [flatten_dict(data, '', current_year, previous_year)]
    elif isinstance(data, list):
        return [flatten_dict(item, '', current_year, previous_year) for item in data if item]
    else:
        return [{'value': str(data)}]

def flatten_dict(data, prefix='', current_year=2024, previous_year=2023):
    """Flatten nested dictionary into key-value pairs"""
    if not isinstance(data, dict):
        return {'value': str(data)}
    
    # Skip amount fields for display
    skip_keys = {'amount_2024', 'amount_2023', 'prior_year_amount', 'taxable_wages', 'interest_income', 'gross_distributions', 'taxable_amount', 'federal_tax_withheld', 'state_tax_withheld', 'benefits_received', 'benefits_repaid', 'medicare_premiums', 'federal_withheld', 'state_withheld'}
    
    flattened = {}
    for key, value in data.items():
        if key in skip_keys:
            continue
            
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                if nested_key not in skip_keys and nested_value not in ('', None, 0, '0'):
                    display_key = f"{format_field_name(key)} - {format_field_name(nested_key)}"
                    flattened[display_key] = str(nested_value)
        elif value not in ('', None, 0, '0'):
            display_key = f"{prefix} {format_field_name(key, current_year, previous_year)}" if prefix else format_field_name(key, current_year, previous_year)
            flattened[display_key] = str(value)
    
    return flattened

def format_field_name(key, current_year=2024, previous_year=2023):
    """Convert field key to readable name with dynamic years"""
    field_map = {
        'first_name': 'First Name',
        'last_name': 'Last Name',
        'dob': 'Date of Birth',
        'ssn': 'SSN',
        'tsj': 'TSJ',
        'employer_name': 'Employer',
        'taxable_wages': 'Taxable Wages',
        'payer_name': 'Payer',
        'account_number': 'Account Number',
        'interest_income': 'Interest Income',
        'us_bonds_obligations': 'US Bonds & Obligations',
        'tax_exempt_interest': 'Tax-Exempt Interest',
        'prior_year_amount': f'{previous_year} Amount',
        'box_1a_ordinary_dividends': 'Ordinary Dividends (1a)',
        'box_1b_qualified_dividends': 'Qualified Dividends (1b)',
        'box_2a_capital_gain': 'Capital Gain (2a)',
        'gross_distributions': 'Gross Distributions',
        'taxable_amount': 'Taxable Amount',
        'federal_tax_withheld': 'Federal Tax Withheld',
        'state_tax_withheld': 'State Tax Withheld',
        'is_rollover': 'Rollover',
        'entity_name': 'Entity Name',
        'employer_id': 'Employer ID',
        'health_insurance_paid': 'Health Insurance Paid',
        'paid_to': 'Paid To',
        'form_1098_received': 'Form 1098 Received',
        'amount_2024': f'{current_year} Amount',
        'amount_2023': f'{previous_year} Amount',
        'description': 'Description',
        'location_of_property': 'Property Location',
        'nature_source': 'Nature/Source',
        'benefits_received': 'Benefits Received',
        'benefits_repaid': 'Benefits Repaid',
        'medicare_premiums': 'Medicare Premiums',
        'federal_withheld': 'Federal Withheld',
        'state_withheld': 'State Withheld',
        'organization_description': 'Organization',
        'method_of_valuation': 'Valuation Method',
        'business_name': 'Business Name',
        'ein': 'EIN',
        'business_address': 'Business Address',
        'accounting_method': 'Accounting Method'
    }
    return field_map.get(key, key.replace('_', ' ').title())

def format_contribution_type(contrib_type):
    """Format contribution type for display"""
    type_map = {
        'cash_100_percent_limit': 'Cash (100% limit)',
        'cash_50_percent_limit': 'Cash (50% limit)',
        'noncash_contributions': 'Non-cash'
    }
    return type_map.get(contrib_type, contrib_type.replace('_', ' ').title())


# -----------------
# S3 utils
# -----------------

def get_s3_client():
    """Get configured S3 client"""
    return boto3.client(
        's3',
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION
    )

def upload_file_to_s3(file_obj, s3_key, content_type='application/pdf'):
    """Upload file to S3 bucket"""
    s3_client = get_s3_client()
    s3_client.upload_fileobj(
        file_obj,
        settings.AWS_STORAGE_BUCKET_NAME,
        s3_key,
        ExtraArgs={'ContentType': content_type}
    )
    return s3_key

def download_file_from_s3(s3_key, local_path=None):
    """Download file from S3 to local path or temporary file"""
    s3_client = get_s3_client()
    
    if not local_path:
        # Create temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        local_path = temp_file.name
        temp_file.close()
    
    s3_client.download_file(
        settings.AWS_STORAGE_BUCKET_NAME,
        s3_key,
        local_path
    )
    return local_path

def get_s3_file_url(s3_key, expiration=3600):
    """Generate a presigned URL for accessing S3 file"""
    s3_client = get_s3_client()
    
    url = s3_client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
            'Key': s3_key
        },
        ExpiresIn=expiration
    )
    return url

def delete_s3_file(s3_key):
    """Delete file from S3 bucket"""
    s3_client = get_s3_client()
    s3_client.delete_object(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=s3_key
    )

# -----------------
# summary generation 
# -----------------

def safe_get(data: Dict, key: str, default: Any = None) -> Any:
    """Safely get value from dictionary with default fallback."""
    return data.get(key, default) if isinstance(data, dict) else default


def get_owner_name(tsj: str, taxpayer: str = "", spouse: str = "") -> str:
    """Convert TSJ code to readable owner name."""
    if not tsj:
        return "TSJ not specified"

    tsj_upper = tsj.upper().strip()
    
    if tsj_upper == 'T':
        return f"{taxpayer} (Taxpayer)" if taxpayer else "Taxpayer"
    elif tsj_upper == 'S':
        return f"{spouse} (Spouse)" if spouse else "Spouse"
    elif tsj_upper == 'J':
        return "Joint"
    return tsj


def extract_personal_info(json_data: List[Dict]) -> Dict[str, str]:
    """Extract taxpayer and spouse information."""
    for page in json_data:
        if not isinstance(page, dict):
            continue
            
        personal_info = safe_get(page, 'data', {}).get('personal_information')
        if personal_info:
            taxpayer = safe_get(personal_info, 'taxpayer', {}).get('first_name', '')
            spouse = safe_get(personal_info, 'spouse', {}).get('first_name', '')
            return {'taxpayer': taxpayer, 'spouse': spouse}
    
    return {'taxpayer': '', 'spouse': ''}


def get_tax_year(json_data: List[Dict]) -> int:
    """Extract tax year from data or use current year - 1."""
    for page in json_data:
        if isinstance(page, dict):
            tax_year = safe_get(page, 'data', {}).get('detected_tax_year')
            if tax_year:
                return tax_year
    
    return datetime.now().year - 1


def process_wages(page_data: Dict, owner_func) -> List[Dict]:
    """Process W-2 wage information."""
    wages = safe_get(page_data, 'dependents_and_wages', [])
    if not isinstance(wages, list):
        return []
    
    result = []
    for item in wages:
        if isinstance(item, dict) and item.get('employer_name'):
            # Try both 'tsj' and 'ts' fields for owner identification
            tsj_value = item.get('tsj') if 'tsj' in item else item.get('ts', '')
            result.append({
                "employer_name": item['employer_name'],
                "owner": owner_func(tsj_value),
                "taxable_wages": item.get('taxable_wages'),
                "federal_tax_withheld": safe_get(item, 'tax_withheld', {}).get('federal')
            })
    return result


def process_interest_income(page_data: Dict, owner_func) -> List[Dict]:
    """Process 1099-INT interest income."""
    interest = safe_get(page_data, 'interest_income', [])
    if not isinstance(interest, list):
        return []
    
    result = []
    for item in interest:
        if isinstance(item, dict) and item.get('payer_name'):
            result.append({
                "payer_name": item['payer_name'],
                "owner": owner_func(item.get('tsj', '')),
                "account_number": item.get('account_number'),
                "interest_income": item.get('interest_income')
            })
    return result


def process_dividend_income(page_data: Dict, owner_func) -> List[Dict]:
    """Process 1099-DIV dividend income."""
    dividend = safe_get(page_data, 'dividend_income', [])
    if not isinstance(dividend, list):
        return []
    
    result = []
    for item in dividend:
        if isinstance(item, dict) and item.get('payer_name'):
            result.append({
                "payer_name": item['payer_name'],
                "owner": owner_func(item.get('tsj', '')),
                "account_number": item.get('account_number'),
                "ordinary_dividends": item.get('box_1a_ordinary_dividends'),
                "qualified_dividends": item.get('box_1b_qualified_dividends')
            })
    return result


def process_ira_distributions(page_data: Dict, owner_func) -> List[Dict]:
    """Process 1099-R IRA distributions."""
    ira = safe_get(page_data, 'ira_distributions', [])
    if not isinstance(ira, list):
        return []
    
    result = []
    for item in ira:
        if isinstance(item, dict) and item.get('payer_name'):
            result.append({
                "payer_name": item['payer_name'],
                "owner": owner_func(item.get('tsj', '')),
                "account_number": item.get('account_number'),
                "gross_distributions": item.get('gross_distributions'),
                "taxable_amount": item.get('taxable_amount')
            })
    return result


def process_brokerage_statement_details(page_data: Dict, owner_func) -> List[Dict]:
    """Process brokerage statement details."""
    brokerage = safe_get(page_data, 'brokerage_statement_details', [])
    if not isinstance(brokerage, list):
        return []

    result = []
    for item in brokerage:
        if isinstance(item, dict) and item.get('payer_name'):
            entry = {
                "payer_name": item['payer_name'],
                "owner": owner_func(item.get('tsj', '')),
                "information_included": item.get('information_included'),
                "interest_income": item.get('interest_income'),
                "us_bonds_obligations": item.get('us_bonds_obligations'),
                "code": item.get('code'),
                "tax_exempt_interest": item.get('tax_exempt_interest'),
                "box_1a_total_ordinary_dividends": item.get('box_1a_total_ordinary_dividends'),
                "box_1b_qualified_dividends": item.get('box_1b_qualified_dividends'),
                "box_2a_total_capital_gain": item.get('box_2a_total_capital_gain'),
                "us_bond_interest_percent": item.get('us_bond_interest_percent')
            }
            # Only include account_number if it exists and is not None
            if item.get('account_number'):
                entry["account_number"] = item['account_number']
            result.append(entry)
    return result


def process_1099b_proceeds(page_data: Dict, owner_func) -> List[Dict]:
    """Process 1099-B proceeds from broker transactions."""
    proceeds = safe_get(page_data, '1099b_proceeds', [])
    if not isinstance(proceeds, list):
        return []

    result = []
    for item in proceeds:
        if isinstance(item, dict) and item.get('payer_name'):
            entry = {
                "payer_name": item['payer_name'],
                "owner": owner_func(item.get('tsj', '')),
                "proceeds": item.get('proceeds'),
                "cost_basis": item.get('cost_basis')
            }
            # Only include account_number if it exists and is not None
            if item.get('account_number'):
                entry["account_number"] = item['account_number']
            result.append(entry)
    return result


def process_mortgage_interest(page_data: Dict, owner_func) -> List[Dict]:
    """Process 1098 mortgage interest."""
    mortgage = safe_get(page_data, 'mortgage_interest', [])
    if not isinstance(mortgage, list):
        return []
    
    result = []
    for item in mortgage:
        if isinstance(item, dict) and item.get('paid_to'):
            result.append({
                "paid_to": item['paid_to'],
                "owner": owner_func(item.get('tsj', '')),
                "account_number": item.get('account_number'),
                "mortgage_interest": item.get('mortgage_interest')
            })
    return result


def process_social_security(page_data: Dict, owner_func) -> List[Dict]:
    """Process SSA-1099 social security benefits."""
    ssb = safe_get(page_data, 'social_security_benefits', [])
    if not isinstance(ssb, list):
        return []
    
    result = []
    for item in ssb:
        if isinstance(item, dict):
            result.append({
                "owner": owner_func(item.get('tsj', '')),
                "benefits_received": item.get('benefits_received'),
                "medicare_premiums": item.get('medicare_premiums'),
                "federal_withheld": item.get('federal_withheld')
            })
    return result


def process_state_tax_refunds(page_data: Dict, owner_func) -> List[Dict]:
    """Process 1099-G state tax refunds."""
    refunds = safe_get(page_data, 'state_tax_refunds', [])
    if not isinstance(refunds, list):
        return []

    result = []
    for item in refunds:
        if isinstance(item, dict) and item.get('state'):
            result.append({
                "state": item['state'],
                "owner": owner_func(item.get('tsj', '')),
                "city": item.get('city'),
                "tax_year": item.get('tax_year'),
                "state_refund": item.get('state_refund'),
                "local_refund": item.get('local_refund')
            })
    return result


def process_other_income_by_type(page_data: Dict, owner_func, income_type: str) -> List[Dict]:
    """Process other income by specific type."""
    other_income = safe_get(page_data, 'other_income', [])
    if not isinstance(other_income, list):
        return []

    result = []
    for item in other_income:
        if isinstance(item, dict) and item.get('nature_source'):
            nature_lower = item.get('nature_source', '').lower()
            if income_type.lower() in nature_lower:
                result.append({
                    "nature_source": item['nature_source'],
                    "owner": owner_func(item.get('tsj', '')),
                    "amount": item.get('amount_2024') or item.get('amount_2023')
                })
    return result


def process_rental_and_royalty_expenses(page_data: Dict, owner_func) -> List[Dict]:
    """Process rental and royalty expenses."""
    expenses = safe_get(page_data, 'rental_and_royalty_expenses', [])
    if not isinstance(expenses, list):
        return []

    result = []
    for item in expenses:
        if isinstance(item, dict) and item.get('description'):
            result.append({
                "description": item['description'],
                "owner": owner_func(item.get('tsj', '')),
                "location_of_property": item.get('location_of_property'),
                "amount": item.get('amount_2024') or item.get('amount_2023')
            })
    return result


def process_rental_and_royalty_property_equipment(page_data: Dict, owner_func) -> List[Dict]:
    """Process rental and royalty property and equipment."""
    property_equipment = safe_get(page_data, 'rental_and_royalty_property_equipment_depletion', [])
    if not isinstance(property_equipment, list):
        return []

    result = []
    for item in property_equipment:
        if isinstance(item, dict) and item.get('description'):
            result.append({
                "description": item['description'],
                "owner": owner_func(item.get('tsj', '')),
                "location_of_property": item.get('location_of_property'),
                "amount": item.get('amount_2024') or item.get('amount_2023')
            })
    return result


def process_student_loan_interest(page_data: Dict, owner_func) -> List[Dict]:
    """Process 1098-E student loan interest."""
    student_loans = safe_get(page_data, 'student_loan_interest', [])
    if not isinstance(student_loans, list):
        return []

    result = []
    for item in student_loans:
        if isinstance(item, dict) and item.get('nature_source'):
            result.append({
                "nature_source": item['nature_source'],
                "owner": owner_func(item.get('tsj', '')),
                "amount": item.get('amount_2024') or item.get('amount_2023')
            })
    return result


def process_education_expenses(page_data: Dict) -> List[Dict]:
    """Process 1098-T tuition statements."""
    education = safe_get(page_data, 'education_expenses', [])
    if not isinstance(education, list):
        return []
    
    result = []
    for item in education:
        if isinstance(item, dict) and item.get('institution'):
            result.append({
                "institution": item['institution'],
                "student_name": item.get('student_name'),
                "tuition_fees": item.get('tuition_fees'),
                "scholarships_grants": item.get('scholarships_grants')
            })
    return result


def process_k1_income(page_data: Dict, owner_func, income_key: str) -> List[Dict]:
    """Process K-1 income (partnership, S-corp, estate/trust, REMIC)."""
    k1_data = safe_get(page_data, income_key, [])
    if not isinstance(k1_data, list):
        return []
    
    result = []
    for item in k1_data:
        if isinstance(item, dict) and item.get('entity_name'):
            result.append({
                "entity_name": item['entity_name'],
                "owner": owner_func(item.get('tsj', '')),
                "employer_id": item.get('employer_id'),
                "health_insurance_paid": item.get('health_insurance_paid')
            })
    return result


def process_deductions(page_data: Dict, owner_func) -> List[Dict]:
    """Process itemized deductions."""
    result = []
    
    # Medical expenses
    medical = safe_get(page_data, 'medical_dental_expenses', [])
    if isinstance(medical, list):
        for item in medical:
            if isinstance(item, dict) and item.get('description'):
                result.append({
                    "description": item['description'],
                    "amount": item.get('amount_2024') or item.get('amount_2023'),
                    "type": "Medical"
                })
    
    # Real estate taxes
    taxes = safe_get(page_data, 'real_estate_taxes', [])
    if isinstance(taxes, list):
        for item in taxes:
            if isinstance(item, dict) and item.get('description'):
                result.append({
                    "description": item['description'],
                    "owner": owner_func(item.get('tsj', '')),
                    "amount": item.get('amount_2024') or item.get('amount_2023'),
                    "type": "Taxes"
                })
    
    return result


def process_contributions(page_data: Dict, owner_func) -> List[Dict]:
    """Process charitable contributions with nested structure."""
    contrib = safe_get(page_data, 'contributions', {})
    if not isinstance(contrib, dict):
        return []

    result = []

    # Process cash contributions (100% limit)
    cash_100 = safe_get(contrib, 'cash_100_percent_limit', [])
    if isinstance(cash_100, list):
        for item in cash_100:
            if isinstance(item, dict) and item.get('organization_description'):
                result.append({
                    "organization_description": item['organization_description'],
                    "owner": owner_func(item.get('tsj', '')),
                    "contribution_type": "Cash (100% limit)",
                    "amount": item.get('amount_2024') or item.get('amount_2023')
                })

    # Process cash contributions (50% limit)
    cash_50 = safe_get(contrib, 'cash_50_percent_limit', [])
    if isinstance(cash_50, list):
        for item in cash_50:
            if isinstance(item, dict) and item.get('organization_description'):
                result.append({
                    "organization_description": item['organization_description'],
                    "owner": owner_func(item.get('tsj', '')),
                    "contribution_type": "Cash (50% limit)",
                    "amount": item.get('amount_2024') or item.get('amount_2023')
                })

    # Process noncash contributions
    noncash = safe_get(contrib, 'noncash_contributions', [])
    if isinstance(noncash, list):
        for item in noncash:
            if isinstance(item, dict) and item.get('organization_description'):
                result.append({
                    "organization_description": item['organization_description'],
                    "owner": owner_func(item.get('tsj', '')),
                    "contribution_type": "Noncash",
                    "method_of_valuation": item.get('method_of_valuation'),
                    "amount": item.get('amount_2024') or item.get('amount_2023')
                })

    return result


def process_business_income(page_data: Dict, owner_func) -> List[Dict]:
    """Process business income and cost of goods sold."""
    business_income = safe_get(page_data, 'business_income_and_cost_of_goods_sold', [])
    if not isinstance(business_income, list):
        return []
    
    result = []
    for item in business_income:
        if isinstance(item, dict) and item.get('business_name'):
            result.append({
                "business_name": item['business_name'],
                "owner": owner_func(item.get('tsj', ''))
            })
    return result


def process_business_expenses(page_data: Dict, owner_func) -> List[Dict]:
    """Process business expenses and property equipment."""
    business_expenses = safe_get(page_data, 'business_expenses_and_property_equipment', [])
    if not isinstance(business_expenses, list):
        return []
    
    result = []
    for item in business_expenses:
        if isinstance(item, dict) and item.get('business_name'):
            result.append({
                "business_name": item['business_name'],
                "owner": owner_func(item.get('tsj', ''))
            })
    return result


def process_business_vehicle_property(page_data: Dict, owner_func) -> List[Dict]:
    """Process business vehicle and listed property."""
    business_vehicle = safe_get(page_data, 'business_vehicle_and_listed_property', [])
    if not isinstance(business_vehicle, list):
        return []
    
    result = []
    for item in business_vehicle:
        if isinstance(item, dict) and item.get('business_name'):
            result.append({
                "business_name": item['business_name'],
                "owner": owner_func(item.get('tsj', ''))
            })
    return result

# -----------------
# Summary Generation View for Tax Organizer 
# -----------------

def generate_summary_dict(json_data: List[Dict]) -> Dict[str, Any]:
    """Generate structured dictionary summary from extracted JSON data."""

    # Defensive check
    if not isinstance(json_data, list):
        return {}

    # Extract basic info
    personal_info = extract_personal_info(json_data)
    tax_year = get_tax_year(json_data)

    # Owner resolver
    def owner_func(tsj):
        return get_owner_name(tsj, personal_info['taxpayer'], personal_info['spouse'])

    # Initialize summary container
    summary = {
        "title": f"TAX DOCUMENT SUMMARY - TAX YEAR {tax_year}",
        "tax_year": tax_year,
        "taxpayer": personal_info['taxpayer'],
        "spouse": personal_info['spouse'],

        # Standard categories
        "wages_and_employment": [],
        "interest_income": [],
        "brokerage_statement_details": [],
        "dividend_income": [],
        "ira_distributions": [],
        "1099b_proceeds": [],
        "mortgage_interest": [],
        "social_security_benefits": [],
        "state_tax_refunds": [],
        "nonemployee_compensation": [],
        "miscellaneous_information": [],
        "payment_card_1099k": [],
        "tuition_statement_1098t": [],
        "student_loan_interest": [],
        "qualified_education_1099q": [],
        "rental_and_royalty_expenses": [],
        "rental_and_royalty_property_equipment": [],
        "partnership_income": [],
        "s_corp_income": [],
        "estate_trust_income": [],
        "remic_income": [],
        "itemized_deductions_medical_taxes": [],
        "contributions": [],
        "business_income_and_cost_of_goods_sold": [],
        "business_expenses_and_property_equipment": [],
        "business_vehicle_and_listed_property": [],
        "other_tax_forms": [],

        # ⭐️ NEW — Custom user-added fields
        "custom_fields": []
    }

    # Process each extracted page
    for page in json_data:
        if not isinstance(page, dict):
            continue

        page_data = safe_get(page, 'data', {})

        # Skip invalid pages
        if page_data.get('extraction_skipped') or page_data.get('extraction_error'):
            continue

        # Add official income categories
        summary["wages_and_employment"].extend(process_wages(page_data, owner_func))
        summary["interest_income"].extend(process_interest_income(page_data, owner_func))
        summary["brokerage_statement_details"].extend(process_brokerage_statement_details(page_data, owner_func))
        summary["dividend_income"].extend(process_dividend_income(page_data, owner_func))
        summary["ira_distributions"].extend(process_ira_distributions(page_data, owner_func))
        summary["1099b_proceeds"].extend(process_1099b_proceeds(page_data, owner_func))
        summary["mortgage_interest"].extend(process_mortgage_interest(page_data, owner_func))
        summary["social_security_benefits"].extend(process_social_security(page_data, owner_func))
        summary["state_tax_refunds"].extend(process_state_tax_refunds(page_data, owner_func))

        # Other income categories
        summary["nonemployee_compensation"].extend(process_other_income_by_type(page_data, owner_func, "nonemployee"))
        summary["miscellaneous_information"].extend(process_other_income_by_type(page_data, owner_func, "misc"))
        summary["payment_card_1099k"].extend(process_other_income_by_type(page_data, owner_func, "1099-k"))
        summary["qualified_education_1099q"].extend(process_other_income_by_type(page_data, owner_func, "1099-q"))

        # Education, rentals, K-1s
        summary["tuition_statement_1098t"].extend(process_education_expenses(page_data))
        summary["student_loan_interest"].extend(process_student_loan_interest(page_data, owner_func))
        summary["rental_and_royalty_expenses"].extend(process_rental_and_royalty_expenses(page_data, owner_func))
        summary["rental_and_royalty_property_equipment"].extend(process_rental_and_royalty_property_equipment(page_data, owner_func))
        summary["partnership_income"].extend(process_k1_income(page_data, owner_func, "partnership_income"))
        summary["s_corp_income"].extend(process_k1_income(page_data, owner_func, "s_corp_income"))
        summary["estate_trust_income"].extend(process_k1_income(page_data, owner_func, "estate_trust_income"))
        summary["remic_income"].extend(process_k1_income(page_data, owner_func, "remic_income"))

        # Deductions & donations
        summary["itemized_deductions_medical_taxes"].extend(process_deductions(page_data, owner_func))
        summary["contributions"].extend(process_contributions(page_data, owner_func))

        # Business sections
        summary["business_income_and_cost_of_goods_sold"].extend(process_business_income(page_data, owner_func))
        summary["business_expenses_and_property_equipment"].extend(process_business_expenses(page_data, owner_func))
        summary["business_vehicle_and_listed_property"].extend(process_business_vehicle_property(page_data, owner_func))

        # Remaining "other" income items
        other_income = safe_get(page_data, 'other_income', [])
        if isinstance(other_income, list):
            for item in other_income:
                if isinstance(item, dict) and item.get('nature_source'):
                    nature_lower = item['nature_source'].lower()
                    if not any(k in nature_lower for k in ["nonemployee", "misc", "1099-k", "1099-q"]):
                        summary["other_tax_forms"].append({
                            "nature_source": item['nature_source'],
                            "owner": owner_func(item.get('tsj', '')),
                            "amount": item.get('amount_2024') or item.get('amount_2023')
                        })

    # ⭐️ NEW — Append custom fields if ExtractedData object included them
    if hasattr(json_data, "custom_fields") and isinstance(json_data.custom_fields, list):
        summary["custom_fields"] = json_data.custom_fields.copy()

    return summary


def generate_summary_text_from_dict(summary_dict: Dict[str, Any]) -> str:
    """Generate human-readable text summary from structured dictionary."""
    if not summary_dict:
        return "No summary data available."
    
    lines = []
    
    # Header
    lines.extend([
        "=" * 80,
        summary_dict.get('title', 'TAX DOCUMENT SUMMARY'),
        "=" * 80,
        ""
    ])
    
    # Personal info
    if summary_dict.get('taxpayer'):
        lines.append(f"Taxpayer: {summary_dict['taxpayer']}")
    if summary_dict.get('spouse'):
        lines.append(f"Spouse: {summary_dict['spouse']}")
    
    lines.extend(["", "=" * 80, ""])
    
    # Document sections
    sections = [
        ("wages_and_employment", "W-2 (Wages)",
         lambda x: f" • {x['owner']} → {x['employer_name']}"),
        ("interest_income", "1099-INT (Interest Income)",
         lambda x: f" • {x['owner']} → {x['payer_name']}" +
                  (f"\n   Account: {x['account_number']}" if x.get('account_number') else "")),
        ("brokerage_statement_details", "Brokerage Statement Details",
         lambda x: f" • {x['owner']} → {x['payer_name']}" +
                  (f"\n   Account: {x['account_number']}" if x.get('account_number') else "")),
        ("dividend_income", "1099-DIV (Dividend and Distribution)",
         lambda x: f" • {x['owner']} → {x['payer_name']}" +
                  (f"\n   Account: {x['account_number']}" if x.get('account_number') else "")),
        ("ira_distributions", "1099-R (IRA Distribution, Pension, Annuities)",
         lambda x: f" • {x['payer_name']}" if x.get('owner') in ['Unknown', 'TSJ not specified'] else f" • {x['owner']} → {x['payer_name']}"),
        ("1099b_proceeds", "1099-B (Proceeds From Broker Transactions)",
         lambda x: f" • {x['owner']} → {x['payer_name']}" +
                  (f"\n   Account: {x['account_number']}" if x.get('account_number') else "")),
        ("mortgage_interest", "Form 1098 (Mortgage Interest Statement)",
         lambda x: f" • {x['owner']} → {x['paid_to']}" +
                  (f"\n   Account: {x['account_number']}" if x.get('account_number') else "")),
        ("social_security_benefits", "Form SSA-1099 (Social Security Benefits)",
         lambda x: f" • {x['owner']} → Social Security Benefits"),
        ("state_tax_refunds", "Form 1099-G (State Tax Refunds)",
         lambda x: f" • {x['owner']} → {x['state']} {x.get('city', '')}"),
        ("nonemployee_compensation", "Form 1099-NEC (Nonemployee Compensation)",
         lambda x: f" • {x['owner']} → {x['nature_source']}"),
        ("miscellaneous_information", "Form 1099-MISC (Miscellaneous Information)",
         lambda x: f" • {x['owner']} → {x['nature_source']}"),
        ("payment_card_1099k", "Form 1099-K (Payment Card and Third Party Network Transactions)",
         lambda x: f" • {x['owner']} → {x['nature_source']}"),
        ("tuition_statement_1098t", "Form 1098-T (Tuition Statement)",
         lambda x: f" • {x.get('student_name', 'Unknown')} → {x['institution']}"),
        ("student_loan_interest", "Form 1098-E (Student Loan Interest)",
         lambda x: f" • {x['owner']} → {x['nature_source']}"),
        ("rental_and_royalty_expenses", "Rental and Royalty Expenses",
         lambda x: f" • {x['owner']} → {x['description']}\n   Location: {x.get('location_of_property', 'N/A')}"),
        ("rental_and_royalty_property_equipment", "Rental and Royalty Property & Equipment",
         lambda x: f" • {x['owner']} → {x['description']}\n   Location: {x.get('location_of_property', 'N/A')}"),
        ("partnership_income", "Partnership Income (K-1)",
         lambda x: f" • {x['owner']} → {x['entity_name']}" +
                  (f"\n   EIN: {x['employer_id']}" if x.get('employer_id') else "")),
        ("s_corp_income", "S Corporation Income (K-1)",
         lambda x: f" • {x['owner']} → {x['entity_name']}" +
                  (f"\n   EIN: {x['employer_id']}" if x.get('employer_id') else "")),
        ("itemized_deductions_medical_taxes", "Itemized Deductions - Medical and Taxes",
         lambda x: f" • {x.get('description', 'N/A')}\n   Owner: {x.get('owner', 'N/A')}"),
        ("contributions", "Itemized Deductions - Contributions (STOP PAGE)",
         lambda x: f" • {x['organization_description']}\n   Owner: {x['owner']}\n   Type: {x.get('contribution_type', 'N/A')}"),
        ("business_income_and_cost_of_goods_sold", "Business Income and Expenses - Income and Cost of Goods Sold",
         lambda x: f" • {x['owner']} → {x['business_name']}"),
        ("business_expenses_and_property_equipment", "Business Income and Expenses - Expenses and Property & Equipment",
         lambda x: f" • {x['owner']} → {x['business_name']}"),
        ("business_vehicle_and_listed_property", "Business Income and Expenses - Vehicle and Listed Property",
         lambda x: f" • {x['owner']} → {x['business_name']}"),
        ("other_tax_forms", "Any other tax-related forms",
         lambda x: f" • {x['owner']} → {x['nature_source']}")
    ]
    
    for key, title, formatter in sections:
        items = summary_dict.get(key, [])
        if items:
            lines.extend([f"\n {title}", "-" * 80])
            for item in items:
                formatted = formatter(item)
                if formatted:
                    lines.append(formatted)
            lines.append("")
    
    # Footer
    lines.extend(["\n" + "=" * 80, "END OF SUMMARY", "=" * 80])
    
    return "\n".join(lines)


# -----------------
# HTML Summary Generation
# -----------------


def generate_summary_html_from_dict(summary_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Return structured data for HTML template rendering."""
    if not summary_dict:
        return {"sections": [], "has_data": False}

    # Define non-business sections
    section_configs = [
        ("wages_and_employment", "W-2 (Wages)"),
        ("interest_income", "1099-INT (Interest Income)"),
        ("brokerage_statement_details", "Brokerage Statement Details"),
        ("dividend_income", "1099-DIV (Dividend and Distribution)"),
        ("ira_distributions", "1099-R (IRA Distribution, Pension, Annuities)"),
        ("1099b_proceeds", "1099-B (Proceeds From Broker Transactions)"),
        ("mortgage_interest", "Form 1098 (Mortgage Interest Statement)"),
        ("social_security_benefits", "Form SSA-1099 (Social Security Benefits)"),
        ("state_tax_refunds", "Form 1099-G (State Tax Refunds)"),
        ("nonemployee_compensation", "Form 1099-NEC (Nonemployee Compensation)"),
        ("miscellaneous_information", "Form 1099-MISC (Miscellaneous Information)"),
        ("payment_card_1099k", "Form 1099-K (Payment Card and Third Party Network Transactions)"),
        ("tuition_statement_1098t", "Form 1098-T (Tuition Statement)"),
        ("student_loan_interest", "Form 1098-E (Student Loan Interest)"),
        ("rental_and_royalty_expenses", "Rental and Royalty Expenses"),
        ("rental_and_royalty_property_equipment", "Rental and Royalty Property & Equipment"),
        ("partnership_income", "Partnership Income (K-1)"),
        ("s_corp_income", "S Corporation Income (K-1)"),
        ("itemized_deductions_medical_taxes", "Itemized Deductions - Medical and Taxes"),
        ("contributions", "Itemized Deductions - Contributions"),
        ("other_tax_forms", "Other Tax Forms")
    ]

    sections = []
    
    # Process non-business sections first
    for key, title in section_configs:
        items = summary_dict.get(key, [])
        if items and isinstance(items, list) and len(items) > 0:
            sections.append({
                "key": key,
                "title": title,
                "items": items
            })
    
    # Group business sections by business name
    business_income = summary_dict.get("business_income_and_cost_of_goods_sold", [])
    business_expenses = summary_dict.get("business_expenses_and_property_equipment", [])
    business_vehicle = summary_dict.get("business_vehicle_and_listed_property", [])
    
    # Combine all business expense types
    all_business_expenses = business_expenses + business_vehicle
    
    # Group by business name
    business_groups = {}
    
    # Add income items
    for income_item in business_income:
        if isinstance(income_item, dict) and income_item.get('business_name'):
            business_name = income_item['business_name']
            if business_name not in business_groups:
                business_groups[business_name] = {'income': [], 'expenses': []}
            business_groups[business_name]['income'].append(income_item)
    
    # Add expense items
    for expense_item in all_business_expenses:
        if isinstance(expense_item, dict) and expense_item.get('business_name'):
            business_name = expense_item['business_name']
            if business_name not in business_groups:
                business_groups[business_name] = {'income': [], 'expenses': []}
            business_groups[business_name]['expenses'].append(expense_item)
    
    # Create single business section with all businesses
    if business_groups:
        all_business_items = []
        for business_name, group_data in business_groups.items():
            if group_data['income'] or group_data['expenses']:
                # Get owner from first available item
                owner = ""
                if group_data['income']:
                    owner = group_data['income'][0].get('owner', '')
                elif group_data['expenses']:
                    owner = group_data['expenses'][0].get('owner', '')
                
                combined_item = {
                    "business_name": business_name,
                    "owner": owner,
                    "has_income": len(group_data['income']) > 0,
                    "has_expenses": len(group_data['expenses']) > 0
                }
                all_business_items.append(combined_item)
        
        if all_business_items:
            sections.append({
                "key": "business_grouped",
                "title": "Business Income and Expenses",
                "items": all_business_items
            })

    return {
        "title": summary_dict.get('title', 'TAX DOCUMENT SUMMARY'),
        "tax_year": summary_dict.get('tax_year'),
        "sections": sections,
        "custom_fields": summary_dict.get('custom_fields', []),
        "has_data": len(sections) > 0
    }
    
def log_activity(user, action, description, document=None):
    """
    Create an activity log entry
    
    Args:
        user: User instance (can be None for system actions)
        action: Action type (must match ACTION_CHOICES)
        description: Short description of the activity
        document: Optional TaxDocument instance
    """
    try:
        organizer_models.ActivityLog.objects.create(
            user=user,
            action=action,
            description=description,
            document=document
        )
    except Exception as e:
        # Log silently, don't break main flow
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to create activity log: {e}")

# ==================================
# Organizer Folder Management
# ==================================

# def create_organizer_folder(organizer_name):
#     """Create organizer folder when organizer is uploaded"""
#     from .models import OrganizerFolder
    
#     # Sanitize folder name
#     safe_name = sanitize_folder_name(organizer_name)
#     folder_path = os.path.join(settings.MEDIA_ROOT, safe_name)
    
#     # Create folder if it doesn't exist
#     os.makedirs(folder_path, exist_ok=True)
    
#     # Save to database
#     organizer_folder, created = OrganizerFolder.objects.get_or_create(
#         client_name=organizer_name,
#         defaults={'folder_path': folder_path}
#     )
    
#     return organizer_folder, created

def find_unsorted_folder(client_name):
    """Find existing unsorted folder for client in format upload_Firstname_Lastname, create if not exists"""
    # Convert client_name "First Last" to "upload_First_Last"
    parts = client_name.split()
    if len(parts) >= 2:
        first = parts[0]
        last = parts[1]
        folder_name = f"upload_{first}_{last}"
    else:
        folder_name = f"upload_{client_name.replace(' ', '_')}"

    folder_path = os.path.join(settings.MEDIA_ROOT, folder_name)

    # Check if exact match exists
    if os.path.exists(folder_path) and os.path.isdir(folder_path):
        return folder_path
    
    # Check for case-insensitive matches in media directory
    media_root = settings.MEDIA_ROOT
    if os.path.exists(media_root):
        for item in os.listdir(media_root):
            item_path = os.path.join(media_root, item)
            if os.path.isdir(item_path) and item.lower() == folder_name.lower():
                return item_path
    
    # Create folder if it doesn't exist
    os.makedirs(folder_path, exist_ok=True)
    return folder_path

def create_sorted_folder(client_name):
    """Create sorted results folder for client"""
    safe_name = sanitize_folder_name(f"Sorted_{client_name}")
    folder_path = os.path.join(settings.MEDIA_ROOT, safe_name)
    
    # Check if exact match exists
    if os.path.exists(folder_path) and os.path.isdir(folder_path):
        return folder_path
    
    # Check for case-insensitive matches in media directory
    media_root = settings.MEDIA_ROOT
    if os.path.exists(media_root):
        for item in os.listdir(media_root):
            item_path = os.path.join(media_root, item)
            if os.path.isdir(item_path) and item.lower() == safe_name.lower():
                return item_path
    
    # Create folder if it doesn't exist
    os.makedirs(folder_path, exist_ok=True)
    
    return folder_path

def create_unsorted_client_folder(client_name):
    """Create unsorted_client_name folder for unclassified files"""
    safe_name = sanitize_folder_name(f"unsorted_{client_name}")
    folder_path = os.path.join(settings.MEDIA_ROOT, safe_name)
    
    # Check if exact match exists
    if os.path.exists(folder_path) and os.path.isdir(folder_path):
        return folder_path
    
    # Check for case-insensitive matches in media directory
    media_root = settings.MEDIA_ROOT
    if os.path.exists(media_root):
        for item in os.listdir(media_root):
            item_path = os.path.join(media_root, item)
            if os.path.isdir(item_path) and item.lower() == safe_name.lower():
                return item_path
    
    # Create folder if it doesn't exist
    os.makedirs(folder_path, exist_ok=True)
    
    return folder_path

# ==================================
# Form Filename Generation
# ==================================

def get_form_prefix(form_type: str) -> str:
    """Return numeric prefix for a given form type based on classification order.
    Mapping follows the exact order from organizer extraction.
    """
    mapping = {
        'dependents_and_wages': '01',
        'w-2': '01',
        'w_2': '01',
        'w-2 wages': '01',
        'w_2 wages': '01',
        '1099-int': '02',
        '1099_int': '02',
        '1099-div': '03',
        '1099_div': '03',
        '1099-r': '04',
        '1099_r': '04',
        '1099-misc': '05',
        '1099_misc': '05',
        '1099-nec': '06',
        '1099_nec': '06',
        '1099-k': '07',
        '1099_k': '07',
        '1099-g': '08',
        '1099_g': '08',
        '1099-q': '09',
        '1099_q': '09',
        '1098': '10',
        '1098-e': '11',
        '1098_e': '11',
        '1098-t': '12',
        '1098_t': '12',
        'brokerage_statement_details': '13',
        'brokerage': '13',
        'partnership_income': '14',
        'partnership k-1': '14',
        'partnership k_1': '14',
        'k-1': '14',
        'k_1': '14',
        's_corp_income': '15',
        's-corp k-1': '15',
        's_corp k_1': '15',
        'estate_trust_income': '16',
        'remic_income': '17',
        'ssa-1099': '18',
        '5498': '19'
    }
    return mapping.get(form_type.lower(), '99')

def generate_sorted_filename(form_type: str, recipient_name: str, payer_name: str) -> str:
    """Generate a standardized filename for a sorted form.
    Example: "W-2 – MANUBHAI – MUNN LLC.pdf"
    """
    # Handle unknown/missing values
    if form_type in ['Unknown', 'Not_found', '']:
        form_type = 'Unknown Form'
    if recipient_name in ['Not_found', 'Unknown', '']:
        recipient_name = 'Unknown Recipient'
    if payer_name in ['Not_found', 'Unknown', '']:
        payer_name = 'Unknown Payer'
    
    # Clean names for display
    clean_payer = re.sub(r'[^\w\s-]', '', payer_name).strip().upper()
    clean_payer = re.sub(r'\s+', ' ', clean_payer)
    
    clean_recipient = re.sub(r'[^\w\s()-]', '', recipient_name).strip()
    clean_recipient = re.sub(r'\s+', ' ', clean_recipient)
    
    # Create filename with proper format
    filename = f"{form_type} – {clean_recipient} – {clean_payer}.pdf"
    return filename

def list_folder_pdfs(folder_path: str) -> list:
    """Return a list of PDF files in the given folder with basic metadata.
    Each entry is a dict: {"filename": ..., "size": ..., "modified": ...}
    """
    import os, datetime
    if not os.path.isdir(folder_path):
        return []
    files = []
    for entry in os.scandir(folder_path):
        if entry.is_file() and entry.name.lower().endswith('.pdf'):
            stat = entry.stat()
            files.append({
                "filename": entry.name,
                "size": stat.st_size,
                "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
    # Sort by filename to keep deterministic order
    files.sort(key=lambda x: x["filename"])
    return files

def sanitize_folder_name(name):
    """Sanitize folder name to be filesystem safe"""
    import re
    # Remove invalid characters
    safe_name = re.sub(r'[<>:"/\\|?*]', '', name)
    # Replace spaces with underscores
    safe_name = re.sub(r'\s+', '_', safe_name)
    # Remove leading/trailing dots and spaces
    safe_name = safe_name.strip('. ')
    return safe_name

# Computes SHA-256 hash of file to detect duplicates

def calculate_file_hash(file_path):
    """Calculate SHA-256 hash of a file"""
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()

def is_file_processed(client_name, file_hash):
    """Check if file hash already exists for client"""
    from .models import ProcessedFileHash
    return ProcessedFileHash.objects.filter(
        client_name=client_name,
        file_hash=file_hash
    ).exists()

def mark_file_processed(client_name, file_hash, file_name):
    """Mark file as processed by storing its hash"""
    from .models import ProcessedFileHash
    ProcessedFileHash.objects.get_or_create(
        client_name=client_name,
        file_hash=file_hash,
        defaults={'file_name': file_name}
    )

# ==================================
# Scanning Unsorted Folder for New PDFs
# ==================================

def scan_unsorted_folder(client_name):
    """Scan unsorted folder for new PDF files"""
    # Find the existing unsorted folder
    unsorted_path = find_unsorted_folder(client_name)

    if not unsorted_path:
        return []

    # Find all PDF files
    pdf_files = glob.glob(os.path.join(unsorted_path, "*.pdf"))
    new_files = []

    for pdf_file in pdf_files:
        file_hash = calculate_file_hash(pdf_file)
        if not is_file_processed(client_name, file_hash):
            new_files.append({
                'path': pdf_file,
                'name': os.path.basename(pdf_file),
                'hash': file_hash
            })

    return new_files

# ==================================
# PDF Processing with Forms App
# ==================================

def process_pdf_with_forms_app(pdf_path, client_name):
    """Process PDF using the forms app logic"""
    from forms.views import detect_form_type, get_form_extractor
    import json
    
    try:
        # Open PDF file
        with open(pdf_path, 'rb') as pdf_file:
            # Detect form type
            detected_form = detect_form_type(pdf_file)
            
            # If unknown form, don't process further
            if detected_form == "Unknown":
                return {
                    'success': False,
                    'form_type': 'Unknown',
                    'error': 'Form type not identified - file remains in unsorted folder'
                }
            
            # Extract form data
            pdf_file.seek(0)
            extractor = get_form_extractor(detected_form, pdf_file)
            extracted_data = extractor.extract()
            
            # Parse the extracted data
            try:
                extracted_json = json.loads(extracted_data)
            except json.JSONDecodeError:
                import re
                json_match = re.search(r'```json\s*(\{.*?\})\s*```', extracted_data, re.DOTALL)
                if json_match:
                    extracted_json = json.loads(json_match.group(1))
                else:
                    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', extracted_data, re.DOTALL)
                    if json_match:
                        extracted_json = json.loads(json_match.group(0))
                    else:
                        extracted_json = {"raw_response": extracted_data}
            
            return {
                'success': True,
                'form_type': detected_form,
                'extracted_data': extracted_json
            }
    
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

# ==================================
# File Movement Helpers
# ==================================

def move_processed_file_to_sorted(pdf_path, client_name, form_data):
    """Copy processed file to sorted folder with proper naming, keeping original in place"""
    sorted_folder = create_sorted_folder(client_name)
    
    # Get form info for naming
    form_number = form_data.get('form_number', 'Unknown')
    payer_name = form_data.get('payer_name') or form_data.get('employer_name', 'Unknown')
    recipient_name = form_data.get('recipient_name') or form_data.get('employee_name', client_name)
    
    # Try to get summary data and use smart renaming
    try:
        # Find document with summary data for this client
        documents = organizer_models.TaxDocument.objects.filter(
            extracted_data__isnull=False
        ).select_related('extracted_data')
        
        summary_data = None
        for doc in documents:
            if hasattr(doc, 'extracted_data') and doc.extracted_data and doc.extracted_data.summary_data:
                # Check if this document matches the client by comparing names
                try:
                    # Get taxpayer name from summary_data directly
                    doc_summary = doc.extracted_data.summary_data
                    doc_taxpayer = doc_summary.get('taxpayer', '').strip()
                    
                    # Also check from extracted data if summary doesn't have taxpayer
                    if not doc_taxpayer and hasattr(doc.extracted_data, 'data') and doc.extracted_data.data:
                        first_page = doc.extracted_data.data[0] if isinstance(doc.extracted_data.data, list) else doc.extracted_data.data
                        taxpayer_info = first_page.get("data", {}).get("personal_information", {}).get("taxpayer")
                        if taxpayer_info:
                            first_name = taxpayer_info.get("first_name", "").strip()
                            last_name = taxpayer_info.get("last_name", "").strip()
                            doc_taxpayer = f"{first_name} {last_name}".strip()
                    
                    # Match client name with document taxpayer
                    if doc_taxpayer and (doc_taxpayer == client_name or client_name in doc_taxpayer or doc_taxpayer in client_name):
                        summary_data = doc_summary
                        break
                except Exception:
                    continue
        
        if summary_data:
            # Copy file first, then rename it
            import shutil
            temp_filename = os.path.basename(pdf_path)
            temp_path = os.path.join(sorted_folder, temp_filename)
            
            # Handle duplicate temp filename
            counter = 1
            while os.path.exists(temp_path):
                name, ext = os.path.splitext(temp_filename)
                temp_filename = f"{name}_{counter}{ext}"
                temp_path = os.path.join(sorted_folder, temp_filename)
                counter += 1
            
            # Copy file with temp name
            shutil.copy2(pdf_path, temp_path)
            
            # Use smart renaming with summary data
            rename_result = rename_file_according_to_summary(
                detected_form_type=form_number,
                detected_data=form_data,
                summary_data=summary_data,
                folder_path=sorted_folder,
                current_filename=temp_filename
            )
            
            if rename_result['success']:
                return os.path.join(sorted_folder, rename_result['new_filename'])
            else:
                # If smart renaming failed, keep the temp file
                return temp_path
    except Exception:
        pass  # Fall back to basic naming
    
    # Fallback to basic filename format
    new_filename = generate_sorted_filename(form_number, recipient_name, payer_name)
    new_path = os.path.join(sorted_folder, new_filename)
    
    # Handle duplicates
    counter = 1
    while os.path.exists(new_path):
        name, ext = os.path.splitext(new_filename)
        base_name = f"{name} ({counter}){ext}"
        new_path = os.path.join(sorted_folder, base_name)
        counter += 1
    
    # Copy file (keep original)
    import shutil
    shutil.copy2(pdf_path, new_path)
    
    return new_path

def move_unclassified_file_to_unsorted(pdf_path, client_name):
    """Copy unclassified file to unsorted_client_name folder, keeping original in place"""
    unsorted_folder = create_unsorted_client_folder(client_name)
    
    # Keep original filename
    filename = os.path.basename(pdf_path)
    new_path = os.path.join(unsorted_folder, filename)
    
    # Handle duplicates
    counter = 1
    while os.path.exists(new_path):
        name, ext = os.path.splitext(filename)
        new_filename = f"{name}_{counter:02d}{ext}"
        new_path = os.path.join(unsorted_folder, new_filename)
        counter += 1
    
    # Copy file (keep original)
    import shutil
    shutil.copy2(pdf_path, new_path)
    return new_path

# -----------------
# Unsorted Files Management for revert back email
# -----------------

def get_remaining_unsorted_files(client_name):
    """
    Return list of remaining unprocessed/unsorted PDF files for a client.
    """
    unsorted_folder = find_unsorted_folder(client_name)
    if not unsorted_folder:
        return []

    pdf_files = glob.glob(os.path.join(unsorted_folder, "*.pdf"))
    remaining = []
    for file_path in pdf_files:
        file_hash = calculate_file_hash(file_path)
        if not is_file_processed(client_name, file_hash):
            remaining.append(os.path.basename(file_path))

    return remaining

def move_non_pdf_files_to_unsorted(client_name):
    """
    Move all non-PDF files from upload folder to unsorted folder during form processing.
    """
    upload_folder = find_unsorted_folder(client_name)
    unsorted_folder = create_unsorted_client_folder(client_name)
    
    if not os.path.exists(upload_folder):
        return
    
    import shutil
    import random
    
    # Get all files in upload folder
    for filename in os.listdir(upload_folder):
        file_path = os.path.join(upload_folder, filename)
        
        # Skip if it's a directory or PDF file
        if os.path.isdir(file_path) or filename.lower().endswith('.pdf'):
            continue
        
        # Generate new filename with unsorted prefix
        counter = random.randint(100, 999)
        name, ext = os.path.splitext(filename)
        new_filename = f"unsorted_{counter}{ext}"
        new_path = os.path.join(unsorted_folder, new_filename)
        
        # Handle duplicates
        while os.path.exists(new_path):
            counter = random.randint(100, 999)
            new_filename = f"unsorted_{counter}{ext}"
            new_path = os.path.join(unsorted_folder, new_filename)
        
        # Move the file
        try:
            shutil.move(file_path, new_path)
        except Exception:
            # If move fails, continue with other files
            continue

# ==================================
# Helper Functions for PDF Renaming
# ==================================

def clean_text(text):
    """Clean and normalize text for comparison"""
    if not text:
        return ""
    # Remove extra whitespace, convert to lowercase, remove special characters
    cleaned = re.sub(r'[^\w\s]', '', str(text).lower().strip())
    return ' '.join(cleaned.split())

def calculate_similarity(s1, s2):
    """Calculate similarity between two strings using SequenceMatcher"""
    if not s1 or not s2:
        return 0.0
    return SequenceMatcher(None, clean_text(s1), clean_text(s2)).ratio()

def generate_filename_from_item(section_title, item):
    """Generate filename from section title and item data"""
    # Get the form type from section title
    form_type = section_title
    
    # Get owner information
    owner = item.get('owner', '').strip()
    
    # Extract entity name based on item type
    entity_name = ""
    if 'employer_name' in item:
        entity_name = item['employer_name']
    elif 'payer_name' in item:
        entity_name = item['payer_name']
    elif 'paid_to' in item:
        entity_name = item['paid_to']
    elif 'entity_name' in item:
        entity_name = item['entity_name']
    elif 'business_name' in item:
        entity_name = item['business_name']
    elif 'organization_description' in item:
        entity_name = item['organization_description']
    elif 'institution' in item:
        entity_name = item['institution']
    elif 'state' in item:
        entity_name = item['state']
    elif 'nature_source' in item:
        entity_name = item['nature_source']
    
    # Clean the entity name for filename
    clean_entity = re.sub(r'[^\w\s-]', '', entity_name).strip().upper()
    clean_entity = re.sub(r'\s+', ' ', clean_entity)
    
    # Build filename parts
    parts = [form_type]
    
    if owner:
        parts.append(owner)
    
    if clean_entity:
        # Add account number if available
        if 'account_number' in item and item['account_number']:
            account = item['account_number']
            parts.append(f"{clean_entity} (Account {account})")
        # Add EIN if available
        elif 'employer_id' in item and item['employer_id']:
            ein = item['employer_id']
            parts.append(f"{clean_entity} (EIN {ein})")
        # Add benefits amount for SSA-1099
        elif 'benefits_received' in item and item['benefits_received']:
            benefits = item['benefits_received']
            parts.append(f"Benefits: ${benefits}")
        else:
            parts.append(clean_entity)
    
    # Join with " – " separator
    filename = " – ".join(parts) + ".pdf"
    
    return filename

def get_section_configs():
    """Get the section configuration mapping form types to summary_data keys"""
    return {
        'W-2': 'wages_and_employment',
        'w-2': 'wages_and_employment',
        '1099-INT': 'interest_income',
        '1099-int': 'interest_income',
        '1099-DIV': 'dividend_income', 
        '1099-div': 'dividend_income',
        '1099-R': 'ira_distributions',
        '1099-r': 'ira_distributions',
        '1099-B': '1099b_proceeds',
        '1099-b': '1099b_proceeds',
        '1099-MISC': 'miscellaneous_information',
        '1099-misc': 'miscellaneous_information',
        '1099-NEC': 'nonemployee_compensation',
        '1099-nec': 'nonemployee_compensation',
        '1099-K': 'payment_card_1099k',
        '1099-k': 'payment_card_1099k',
        '1099-G': 'state_tax_refunds',
        '1099-g': 'state_tax_refunds',
        '1099-Q': 'qualified_education_1099q',
        '1099-q': 'qualified_education_1099q',
        '1098': 'mortgage_interest',
        '1098-E': 'student_loan_interest',
        '1098-e': 'student_loan_interest',
        '1098-T': 'tuition_statement_1098t',
        '1098-t': 'tuition_statement_1098t',
        'SSA-1099': 'social_security_benefits',
        'ssa-1099': 'social_security_benefits',
        'K-1': 'partnership_income',
        'k-1': 'partnership_income',
        'Brokerage': 'brokerage_statement_details',
        'brokerage': 'brokerage_statement_details',
        '5498': 'ira_distributions',
    }

def find_best_match_in_summary(detected_form_type, detected_data, summary_data):
    """Find the best matching item in summary_data for the detected form"""
    section_configs = get_section_configs()
    
    # Get the summary section key for this form type
    section_key = section_configs.get(detected_form_type)
    if not section_key or section_key not in summary_data:
        return None, None
    
    summary_items = summary_data[section_key]
    if not summary_items:
        return None, None
    
    # Extract names from detected data for comparison
    detected_names = []
    name_fields = [
        'employer_name', 'payer_name', 'paid_to', 'entity_name', 
        'business_name', 'organization_description', 'institution',
        'state', 'nature_source', 'recipient_name', 'employee_name'
    ]
    
    for field in name_fields:
        if field in detected_data and detected_data[field]:
            detected_names.append(clean_text(detected_data[field]))
    
    if not detected_names:
        return None, None
    
    # Find best match using fuzzy matching
    best_match = None
    best_score = 0
    
    # Section title mapping
    section_title_map = {
        'wages_and_employment': 'W-2 (Wages)',
        'interest_income': '1099-INT (Interest Income)',
        'dividend_income': '1099-DIV (Dividend and Distribution)',
        'ira_distributions': '1099-R (IRA Distribution, Pension, Annuities)',
        '1099b_proceeds': '1099-B (Proceeds From Broker Transactions)',
        'mortgage_interest': 'Form 1098 (Mortgage Interest Statement)',
        'social_security_benefits': 'Form SSA-1099 (Social Security Benefits)',
        'state_tax_refunds': 'Form 1099-G (State Tax Refunds)',
        'nonemployee_compensation': 'Form 1099-NEC (Nonemployee Compensation)',
        'miscellaneous_information': 'Form 1099-MISC (Miscellaneous Information)',
        'payment_card_1099k': 'Form 1099-K (Payment Card and Third Party Network Transactions)',
        'tuition_statement_1098t': 'Form 1098-T (Tuition Statement)',
        'student_loan_interest': 'Form 1098-E (Student Loan Interest)',
        'qualified_education_1099q': 'Form 1099-Q (Qualified Education)',
        'partnership_income': 'Partnership Income (K-1)',
        's_corp_income': 'S Corporation Income (K-1)',
        'estate_trust_income': 'Estate and Trust Income (K-1)',
        'remic_income': 'REMIC Income (K-1)',
        'brokerage_statement_details': 'Brokerage Statement Details',
    }
    
    section_title = section_title_map.get(section_key, detected_form_type)
    
    for item in summary_items:
        if not isinstance(item, dict):
            continue
            
        # Extract names from summary item
        summary_names = []
        for field in name_fields:
            if field in item and item[field]:
                summary_names.append(clean_text(item[field]))
        
        # Compare all detected names with all summary names
        for detected_name in detected_names:
            for summary_name in summary_names:
                similarity = calculate_similarity(detected_name, summary_name)
                if similarity > best_score:
                    best_score = similarity
                    best_match = item
    
    # Return match only if similarity is above threshold
    if best_score >= 0.5:
        return best_match, section_title
    
    return None, None

# Logic Key-Value pair

def create_document_logic_from_summary(extracted_data, taxpayer_name, sorted_folder_path):
    """Create logic key-value pair in extracted_data based on summary_data"""
    try:
        summary_data = extracted_data.get('summary_data', {})
        if not summary_data:
            extracted_data['logic'] = {'status': 'error', 'message': 'No summary_data found'}
            return extracted_data
        
        # Section configurations from reference code
        section_configs = [
            ("wages_and_employment", "W-2 (Wages)"),
            ("interest_income", "1099-INT (Interest Income)"),
            ("brokerage_statement_details", "Brokerage Statement Details"),
            ("dividend_income", "1099-DIV (Dividend and Distribution)"),
            ("ira_distributions", "1099-R (IRA Distribution, Pension, Annuities)"),
            ("1099b_proceeds", "1099-B (Proceeds From Broker Transactions)"),
            ("social_security_benefits", "Form SSA-1099 (Social Security Benefits)"),
            ("state_tax_refunds", "Form 1099-G (State Tax Refunds)"),
            ("nonemployee_compensation", "Form 1099-NEC (Nonemployee Compensation)"),
            ("miscellaneous_information", "Form 1099-MISC (Miscellaneous Information)"),
            ("payment_card_1099k", "Form 1099-K (Payment Card and Third Party Network Transactions)"),
            ("qualified_education_1099q", "Form 1099-Q (Qualified Education Programs)"),
            ("rental_and_royalty_expenses", "Rental and Royalty Expenses"),
            ("rental_and_royalty_property_equipment", "Rental and Royalty Property & Equipment"),
            ("partnership_income", "Partnership Income (K-1)"),
            ("s_corp_income", "S Corporation Income (K-1)"),
            ("estate_trust_income", "Estate/Trust Income (K-1)"),
            ("remic_income", "REMIC Income (K-1)"),
            ("mortgage_interest", "Form 1098 (Mortgage Interest Statement)"),
            ("itemized_deductions_medical_taxes", "Itemized Deductions - Medical and Taxes"),
            ("contributions", "Itemized Deductions - Contributions"),
            ("tuition_statement_1098t", "Form 1098-T (Tuition Statement)"),
            ("student_loan_interest", "Form 1098-E (Student Loan Interest)"),
            ("other_tax_forms", "Other Tax Forms"),
        ]
        
        document_status = {}
        
        # Process each section
        for key, title in section_configs:
            items = summary_data.get(key, [])
            if items and isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        filename = generate_filename_from_item_ref(title, item)
                        found, matched_file, confidence = find_best_matching_file_ref(
                            sorted_folder_path, filename
                        )
                        
                        if found:
                            document_status[filename] = {
                                "status": "sorted",
                                "matched_file": matched_file,
                                "confidence": round(confidence, 2)
                            }
                        else:
                            document_status[filename] = {
                                "status": "required",
                                "matched_file": None,
                                "confidence": 0.0
                            }
        
        # Handle business income sections
        business_income = summary_data.get("business_income_and_cost_of_goods_sold", [])
        business_expenses = summary_data.get("business_expenses_and_property_equipment", [])
        business_vehicle = summary_data.get("business_vehicle_and_listed_property", [])
        
        all_business = business_income + business_expenses + business_vehicle
        business_names = set()
        
        for item in all_business:
            if isinstance(item, dict) and item.get("business_name"):
                business_names.add(item["business_name"])
        
        for business_name in business_names:
            filename = f"Business Income and Expenses - {business_name}"
            found, matched_file, confidence = find_best_matching_file_ref(
                sorted_folder_path, filename
            )
            
            if found:
                document_status[filename] = {
                    "status": "sorted",
                    "matched_file": matched_file,
                    "confidence": round(confidence, 2)
                }
            else:
                document_status[filename] = {
                    "status": "required",
                    "matched_file": None,
                    "confidence": 0.0
                }
        
        # Create logic result
        extracted_data['logic'] = {
            'status': 'success',
            'sorted_folder': f"Sorted_{taxpayer_name}",
            'folder_exists': os.path.exists(sorted_folder_path),
            'total_documents': len(document_status),
            'sorted_count': sum(1 for v in document_status.values() if v['status'] == 'sorted'),
            'required_count': sum(1 for v in document_status.values() if v['status'] == 'required'),
            'data': document_status
        }
        
        return extracted_data
        
    except Exception as e:
        extracted_data['logic'] = {'status': 'error', 'message': str(e)}
        return extracted_data

def generate_filename_from_item_ref(section_title, item):
    """Generate filename based on section type and item data (from reference code)"""
    filename_parts = [section_title]
    
    priority_fields = [
        'owner', 'employer_name', 'payer_name', 'paid_to', 
        'entity_name', 'business_name', 'description', 
        'location_of_property', 'account_number'
    ]
    
    for key in priority_fields:
        if key in item and item[key]:
            value = str(item[key]).strip()
            if (value and 
                value.lower() != "null" and 
                value != "TSJ not specified" and
                value != "not specified"):
                filename_parts.append(value)
    
    return " - ".join(filename_parts)

def find_best_matching_file_ref(folder_path, search_filename):
    """Find best matching file using fuzzy matching (from reference code)"""
    if not os.path.exists(folder_path):
        return False, "", 0.0
    
    last_section = extract_last_section_ref(search_filename)
    if not last_section:
        last_section = search_filename
    
    search_keywords = extract_keywords_ref(last_section)
    best_score = 0.0
    best_filename = ""
    
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        if os.path.isfile(file_path):
            is_keyword_match, keyword_score = check_keyword_match_ref(search_keywords, filename)
            similarity_score = calculate_similarity(last_section, filename)
            
            identifier_score = 0.0
            for keyword in search_keywords:
                if len(keyword) > 2 and keyword in clean_text(filename):
                    identifier_score += 0.3
            
            combined_score = (keyword_score * 0.4) + (similarity_score * 0.4) + (identifier_score * 0.2)
            
            if combined_score > best_score:
                best_score = combined_score
                best_filename = filename
    
    if best_score >= 0.4:
        return True, best_filename, best_score
    
    return False, "", 0.0

def extract_last_section_ref(filename):
    """Extract last section after taxpayer/spouse/owner name"""
    parts = re.split(r'\s*[-–—]\s*', filename)
    
    if len(parts) >= 3:
        last_part = parts[-1].strip()
        last_part_lower = last_part.lower()
        owner_keywords = ['taxpayer', 'spouse', 'joint', 'tsj not specified', 'not specified']
        
        if any(keyword in last_part_lower for keyword in owner_keywords):
            return ""
        
        return last_part
    
    return ""

def extract_keywords_ref(filename):
    """Extract important keywords from filename"""
    cleaned = clean_text(filename)
    
    stop_words = {'the', 'and', 'or', 'for', 'from', 'with', 'tsj', 'not', 'specified', 
                  'taxpayer', 'spouse', 'joint', 'form', 'income', 'property'}
    
    words = cleaned.split()
    keywords = [w for w in words if w not in stop_words and len(w) > 2]
    
    return keywords

def check_keyword_match_ref(search_keywords, filename):
    """Check if keywords match with filename"""
    filename_lower = clean_text(filename)
    
    matches = sum(1 for keyword in search_keywords if keyword in filename_lower)
    
    if len(search_keywords) == 0:
        return False, 0.0
    
    match_percentage = matches / len(search_keywords)
    is_match = match_percentage >= 0.5
    
    return is_match, match_percentage

def rename_file_according_to_summary(detected_form_type, detected_data, summary_data, folder_path, current_filename):
    """Rename PDF file based on summary data matching"""
    try:
        # Find matching item in summary data
        matched_item, section_title = find_best_match_in_summary(
            detected_form_type, detected_data, summary_data
        )
        
        if not matched_item or not section_title:
            return {
                'success': False,
                'error': 'No matching item found in summary data',
                'current_filename': current_filename
            }
        
        # Generate new filename
        new_filename = generate_filename_from_item(section_title, matched_item)
        
        # Construct full paths
        current_path = os.path.join(folder_path, current_filename)
        new_path = os.path.join(folder_path, new_filename)
        
        # Check if current file exists
        if not os.path.exists(current_path):
            return {
                'success': False,
                'error': f'Current file does not exist: {current_filename}',
                'current_filename': current_filename
            }
        
        # Handle duplicate filenames
        counter = 1
        while os.path.exists(new_path) and new_path != current_path:
            name, ext = os.path.splitext(new_filename)
            new_filename_with_counter = f"{name} ({counter}){ext}"
            new_path = os.path.join(folder_path, new_filename_with_counter)
            counter += 1
        
        # Rename the file
        if current_path != new_path:
            os.rename(current_path, new_path)
            final_filename = os.path.basename(new_path)
        else:
            final_filename = current_filename
        
        return {
            'success': True,
            'old_filename': current_filename,
            'new_filename': final_filename,
            'matched_item': matched_item,
            'section_title': section_title
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': f'Error renaming file: {str(e)}',
            'current_filename': current_filename
        }

