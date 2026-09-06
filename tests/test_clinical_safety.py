"""Pruebas del motor de seguridad clínica.

Es el componente cuyo fallo puede dañar directamente a un paciente: si no detecta
una alergia, se receta el medicamento al que el paciente reacciona. Las pruebas
cubren tanto lo que debe detectar como lo que **no** debe bloquear sin motivo,
porque una alerta falsa repetida entrena al profesional a ignorarlas todas.
"""

import pytest

from clinical_safety import (
    BLOCK, WARN, evaluate_prescription, check_allergies, check_interactions,
    check_duplicate_therapy, check_pregnancy, check_quantities, check_pediatric,
    normalize_drug, drug_class,
)


class FakeAllergy:
    def __init__(self, substance, reaction=None, severity='grave', status='confirmada'):
        self.substance = substance
        self.reaction = reaction
        self.severity = severity
        self.status = status


def med(name, quantity=10):
    return {'nombre_med': name, 'medicamento': name, 'cantidad': quantity}


# --- Normalización -----------------------------------------------------------

class TestNormalization:

    @pytest.mark.parametrize('raw,expected', [
        ('Amoxicilina 500 mg', 'amoxicilina'),
        ('AMOXICILINA', 'amoxicilina'),
        ('amoxicilina 500mg cap.', 'amoxicilina'),
        ('Ibuprofeno 400 mg tabletas', 'ibuprofeno'),
        ('Acetaminofén 500mg', 'paracetamol'),
        ('Aspirina', 'acido acetilsalicilico'),
        ('  Metformina   clorhidrato  850 mg ', 'metformina'),
    ])
    def test_normalizes_to_active_ingredient(self, raw, expected):
        assert normalize_drug(raw) == expected

    def test_empty_input_is_safe(self):
        assert normalize_drug('') == ''
        assert normalize_drug(None) == ''

    def test_unknown_drug_normalizes_without_crashing(self):
        # Un medicamento fuera del catálogo debe pasar sin excepción; el motor
        # simplemente no tendrá reglas sobre él.
        assert normalize_drug('Medicamento Inexistente 10mg') == 'medicamento inexistente'
        assert drug_class('Medicamento Inexistente') is None


# --- Alergias ----------------------------------------------------------------

class TestAllergies:

    def test_exact_match_blocks(self):
        findings = check_allergies(
            [med('Amoxicilina 500mg')],
            [FakeAllergy('Amoxicilina', reaction='Urticaria generalizada')],
        )
        assert len(findings) == 1
        assert findings[0].severity == BLOCK
        assert 'Urticaria generalizada' in findings[0].detail

    def test_same_class_blocks(self):
        # Alérgico a amoxicilina, se receta ampicilina: misma familia.
        findings = check_allergies([med('Ampicilina')], [FakeAllergy('Amoxicilina')])
        assert len(findings) == 1
        assert findings[0].severity == BLOCK

    def test_betalactam_cross_reactivity_blocks(self):
        # Alérgico a penicilina, se receta cefalexina: reactividad cruzada.
        findings = check_allergies([med('Cefalexina')], [FakeAllergy('Penicilina')])
        assert len(findings) == 1
        assert findings[0].severity == BLOCK
        assert findings[0].category == 'alergia_cruzada'

    def test_carbapenem_cross_reactivity_warns_not_blocks(self):
        # La reactividad con carbapenémicos es de baja frecuencia: advierte,
        # no bloquea, porque bloquear cerraría una opción terapéutica válida.
        findings = check_allergies([med('Meropenem')], [FakeAllergy('Penicilina')])
        assert len(findings) == 1
        assert findings[0].severity == WARN

    def test_nsaid_cross_reactivity(self):
        findings = check_allergies([med('Ibuprofeno')], [FakeAllergy('Naproxeno')])
        assert findings
        assert findings[0].severity == BLOCK

    def test_unrelated_drug_produces_no_finding(self):
        # Alérgico a penicilina, se receta paracetamol: no hay relación.
        findings = check_allergies([med('Paracetamol')], [FakeAllergy('Penicilina')])
        assert findings == []

    def test_no_allergies_produces_no_finding(self):
        assert check_allergies([med('Amoxicilina')], []) == []
        assert check_allergies([med('Amoxicilina')], None) == []

    def test_dosage_variation_still_matches(self):
        # El paciente es alérgico a "penicilina"; se receta con presentación.
        findings = check_allergies(
            [med('Penicilina benzatinica 1.200.000 UI ampolla')],
            [FakeAllergy('penicilina')],
        )
        assert findings
        assert findings[0].severity == BLOCK


# --- Interacciones -----------------------------------------------------------

class TestInteractions:

    def test_warfarin_and_aspirin_blocks(self):
        findings = check_interactions([med('Warfarina'), med('Aspirina')])
        assert len(findings) == 1
        assert findings[0].severity == BLOCK
        assert 'hemorragia' in findings[0].recommendation.lower()

    def test_interaction_with_active_treatment(self):
        # El paciente ya toma warfarina; se le receta ibuprofeno.
        findings = check_interactions([med('Ibuprofeno')], active_medications=['Warfarina'])
        assert len(findings) == 1
        assert findings[0].severity == BLOCK

    def test_ssri_and_tramadol_blocks(self):
        findings = check_interactions([med('Fluoxetina'), med('Tramadol')])
        assert findings
        assert findings[0].severity == BLOCK
        assert 'serotoninergico' in findings[0].recommendation.lower()

    def test_class_level_interaction_detected(self):
        # Rivaroxabán (anticoagulante) con diclofenaco (AINE): no hay regla para
        # ese par concreto, pero sí para las clases.
        findings = check_interactions([med('Rivaroxaban'), med('Diclofenaco')])
        assert findings
        assert findings[0].severity == BLOCK

    def test_double_raas_blockade_blocks(self):
        findings = check_interactions([med('Enalapril'), med('Losartan')])
        assert findings
        assert findings[0].severity == BLOCK

    def test_benzodiazepine_opioid_blocks(self):
        findings = check_interactions([med('Diazepam'), med('Morfina')])
        assert findings
        assert findings[0].severity == BLOCK

    def test_pair_reported_once(self):
        # El mismo par no debe generar dos hallazgos por estar en ambos sentidos.
        findings = check_interactions(
            [med('Warfarina'), med('Aspirina')], active_medications=['Aspirina'],
        )
        assert len(findings) == 1

    def test_safe_combination_produces_nothing(self):
        findings = check_interactions([med('Paracetamol'), med('Loratadina')])
        assert findings == []


# --- Duplicidad terapéutica --------------------------------------------------

class TestDuplicateTherapy:

    def test_two_nsaids_blocks(self):
        findings = check_duplicate_therapy([med('Ibuprofeno'), med('Naproxeno')])
        assert findings
        assert findings[0].severity == BLOCK

    def test_duplicate_with_active_treatment(self):
        findings = check_duplicate_therapy(
            [med('Enalapril')], active_medications=['Captopril'],
        )
        assert findings
        assert findings[0].severity == BLOCK

    def test_same_drug_twice_is_not_duplicate_therapy(self):
        # Dos líneas del mismo principio activo no son duplicidad de clase; la
        # comprobación de cantidad se ocupa de eso.
        findings = check_duplicate_therapy([med('Ibuprofeno'), med('Ibuprofeno')])
        assert findings == []

    def test_analgesic_duplication_only_warns(self):
        # Paracetamol y dipirona juntos es una pauta de rescate habitual.
        findings = check_duplicate_therapy([med('Paracetamol'), med('Dipirona')])
        assert findings
        assert findings[0].severity == WARN


# --- Embarazo ----------------------------------------------------------------

class TestPregnancy:

    def test_contraindicated_drug_blocks(self):
        findings = check_pregnancy([med('Warfarina')], is_pregnant=True)
        assert findings
        assert findings[0].severity == BLOCK

    def test_ace_inhibitor_blocks(self):
        findings = check_pregnancy([med('Enalapril')], is_pregnant=True)
        assert findings
        assert findings[0].severity == BLOCK

    def test_nsaid_warns(self):
        findings = check_pregnancy([med('Ibuprofeno')], is_pregnant=True)
        assert findings
        assert findings[0].severity == WARN

    def test_not_pregnant_produces_nothing(self):
        assert check_pregnancy([med('Warfarina')], is_pregnant=False) == []

    def test_safe_drug_in_pregnancy(self):
        assert check_pregnancy([med('Paracetamol')], is_pregnant=True) == []


# --- Cantidades y control especial -------------------------------------------

class TestQuantities:

    def test_absurd_quantity_blocks(self):
        findings = check_quantities([med('Paracetamol', quantity=5000)])
        blocking = [f for f in findings if f.severity == BLOCK]
        assert blocking

    def test_zero_quantity_blocks(self):
        findings = check_quantities([med('Paracetamol', quantity=0)])
        assert any(f.severity == BLOCK for f in findings)

    def test_high_quantity_warns(self):
        findings = check_quantities([med('Paracetamol', quantity=300)])
        assert any(f.severity == WARN and f.category == 'cantidad' for f in findings)

    def test_normal_quantity_is_clean(self):
        findings = check_quantities([med('Paracetamol', quantity=20)])
        assert findings == []

    def test_controlled_substance_flagged(self):
        findings = check_quantities([med('Morfina', quantity=10)])
        controlled = [f for f in findings if f.category == 'control_especial']
        assert controlled
        assert 'receta oficial' in controlled[0].detail.lower()

    def test_controlled_substance_lower_quantity_threshold(self):
        # 60 unidades es normal para paracetamol pero alto para un controlado.
        assert check_quantities([med('Paracetamol', quantity=60)]) == []
        findings = check_quantities([med('Morfina', quantity=60)])
        assert any(f.category == 'cantidad' for f in findings)


# --- Pediatría ---------------------------------------------------------------

class TestPediatric:

    def test_aspirin_in_child_blocks(self):
        findings = check_pediatric([med('Aspirina')], patient_age_years=8)
        assert findings
        assert findings[0].severity == BLOCK
        assert 'reye' in findings[0].detail.lower()

    def test_tramadol_in_child_blocks(self):
        findings = check_pediatric([med('Tramadol')], patient_age_years=9)
        assert findings
        assert findings[0].severity == BLOCK

    def test_adult_unaffected(self):
        assert check_pediatric([med('Aspirina')], patient_age_years=45) == []

    def test_unknown_age_produces_nothing(self):
        # Sin edad no se puede afirmar nada; se prefiere no emitir una alerta
        # que el profesional no puede evaluar.
        assert check_pediatric([med('Aspirina')], patient_age_years=None) == []

    def test_age_boundary_respected(self):
        # La doxiciclina se limita en menores de 8 años.
        assert check_pediatric([med('Doxiciclina')], patient_age_years=7)
        assert check_pediatric([med('Doxiciclina')], patient_age_years=8) == []


# --- Informe completo --------------------------------------------------------

class TestFullEvaluation:

    def test_clean_prescription(self):
        report = evaluate_prescription([med('Paracetamol', 20)])
        assert report.is_clear
        assert not report.requires_override
        assert report.summary_line() == 'Sin hallazgos de seguridad clinica.'

    def test_requires_override_when_blocking(self):
        report = evaluate_prescription(
            [med('Amoxicilina')], allergies=[FakeAllergy('Penicilina')],
        )
        assert report.requires_override
        assert report.blocking

    def test_findings_sorted_by_severity(self):
        report = evaluate_prescription(
            [med('Warfarina'), med('Aspirina'), med('Paracetamol', 300)],
        )
        severities = [f.severity for f in report.findings]
        # Los bloqueantes aparecen primero, para que se lean antes.
        assert severities == sorted(severities, key=lambda s: {'block': 0, 'warn': 1, 'info': 2}[s])

    def test_serializable_for_storage(self):
        # El informe se guarda en la orden como JSON: debe poder serializarse.
        import json
        report = evaluate_prescription(
            [med('Ibuprofeno')], allergies=[FakeAllergy('Naproxeno')],
        )
        payload = json.dumps(report.to_dict(), ensure_ascii=False)
        restored = json.loads(payload)
        assert restored['requires_override'] is True
        assert restored['knowledge_base_version']

    def test_multiple_risks_all_reported(self):
        report = evaluate_prescription(
            medications=[med('Warfarina'), med('Ibuprofeno')],
            allergies=[FakeAllergy('Naproxeno')],
            is_pregnant=True,
        )
        categories = {f.category for f in report.findings}
        # Los tres riesgos deben aparecer a la vez: la interacción
        # warfarina-AINE, la alergia (naproxeno e ibuprofeno son ambos AINE, así
        # que se clasifica como misma familia, no como reactividad cruzada entre
        # familias distintas) y la contraindicación en el embarazo.
        assert 'interaccion' in categories
        assert 'alergia' in categories
        assert 'embarazo' in categories

    def test_empty_prescription_is_clear(self):
        assert evaluate_prescription([]).is_clear
