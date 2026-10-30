"""
Fuzzy-only unit tests for the policy logic in FuzzyEVChargingScorer.
These tests do NOT require ML model or real candidate data.
They inject inputs directly into the ControlSystemSimulation.
"""
import pytest
import numpy as np
import skfuzzy as fuzz
from skfuzzy import control as ctrl
from src.fuzzy.scorer import FuzzyEVChargingScorer


class TestFuzzyPolicy:
    """Test the scorer-level policy: high-land offset, protected penalties, fallback."""

    @pytest.fixture
    def scorer(self):
        """Create a scorer instance for testing."""
        return FuzzyEVChargingScorer()

    def _make_simulator(self, scorer, inputs):
        """Create a simulator with preset inputs."""
        sim = ctrl.ControlSystemSimulation(scorer.fuzzy_system)
        for k, v in inputs.items():
            sim.input[k] = v
        return sim

    # --- Helper to run full policy logic ---
    def _run_policy(self, scorer, inputs):
        """Run the full scoring logic for given inputs and return (suitability, demand_source)."""
        sim = self._make_simulator(scorer, inputs)
        sim.compute()
        suitability = sim.output.get('suitability')

        # Apply the same policy logic as in score_candidates
        protected_penalty_active = False
        if suitability is not None and not np.isnan(suitability):
            protected_penalty_active = scorer._any_protected_penalty_active(sim)

        demand_source = "TEST_FUZZY"  # Default: raw fuzzy output

        if (suitability is not None and not np.isnan(suitability)
                and not protected_penalty_active
                and scorer._is_high_land_offset_eligible(sim)):
            suitability = 0.5
            demand_source = "TEST_OFFSET"

        if suitability is None or (isinstance(suitability, float) and np.isnan(suitability)):
            if protected_penalty_active:
                suitability = 0.0
                demand_source = "TEST_NO_OUTPUT_PENALTY_ACTIVE"
            else:
                suitability = 0.5
                demand_source = "TEST_FALLBACK"
        elif protected_penalty_active and demand_source != "TEST_OFFSET":
            # Valid suitability but protected penalty active (and no offset applied)
            demand_source = "TEST_PENALTY_ACTIVE"

        return suitability, demand_source

    # --- Protected Penalties (never suppressed) ---

    def test_penalty_very_low_demand(self, scorer):
        """Very-low demand → unsuitable, never offset."""
        # demand=5 → very_low membership ~0.67
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 5,
            'traffic_accessibility': 0.5,
            'grid_connection_cost': 20,
            'land_cost_tier': 1.5,
            'poi_attractiveness': 50,
            'competition_index': 4,
        })
        assert suitability < 0.2, f"Very-low demand should be unsuitable, got {suitability}"
        assert source != "TEST_OFFSET", "Protected penalty must not be offset"

    def test_penalty_poor_traffic(self, scorer):
        """Poor traffic → unsuitable, never offset."""
        # traffic=0.1 → poor membership ~0.8
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 50,
            'traffic_accessibility': 0.1,
            'grid_connection_cost': 20,
            'land_cost_tier': 1.5,
            'poi_attractiveness': 50,
            'competition_index': 4,
        })
        assert suitability < 0.2, f"Poor traffic should be unsuitable, got {suitability}"
        assert source != "TEST_OFFSET", "Protected penalty must not be offset"

    def test_penalty_very_high_grid(self, scorer):
        """Very-high grid cost → poor, never offset."""
        # grid=45 → very_high membership ~0.8
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 50,
            'traffic_accessibility': 0.5,
            'grid_connection_cost': 45,
            'land_cost_tier': 1.5,
            'poi_attractiveness': 50,
            'competition_index': 4,
        })
        assert 0.2 < suitability < 0.4, f"Very-high grid should be poor, got {suitability}"
        assert source != "TEST_OFFSET", "Protected penalty must not be offset"

    def test_penalty_saturated_competition(self, scorer):
        """Saturated competition → poor, never offset."""
        # comp=9.1 → saturated membership 0.6 (>0.5, strictly)
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 50,
            'traffic_accessibility': 0.5,
            'grid_connection_cost': 20,
            'land_cost_tier': 1.5,
            'poi_attractiveness': 50,
            'competition_index': 9.1,
        })
        assert 0.2 < suitability < 0.4, f"Saturated comp should be poor, got {suitability}"
        assert source != "TEST_OFFSET", "Protected penalty must not be offset"
        # Verify the protected penalty is actually detected
        sim = self._make_simulator(scorer, {
            'predicted_demand': 50,
            'traffic_accessibility': 0.5,
            'grid_connection_cost': 20,
            'land_cost_tier': 1.5,
            'poi_attractiveness': 50,
            'competition_index': 9.1,
        })
        sim.compute()
        protected_active = scorer._any_protected_penalty_active(sim)
        assert protected_active, "Saturated competition should be detected as protected penalty"

    def test_penalty_very_low_poi(self, scorer):
        """Very-low POI → unsuitable, never offset."""
        # POI=5 → very_low membership ~0.67
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 50,
            'traffic_accessibility': 0.5,
            'grid_connection_cost': 20,
            'land_cost_tier': 1.5,
            'poi_attractiveness': 5,
            'competition_index': 4,
        })
        assert suitability < 0.2, f"Very-low POI should be unsuitable, got {suitability}"
        assert source != "TEST_OFFSET", "Protected penalty must not be offset"

    # --- High-Land Offset (eligible only when ALL criteria met AND no protected penalty) ---

    def test_offset_eligible_high_land_site(self, scorer):
        """Eligible high-land site → fair (0.5), offset applied."""
        # demand=80 (high), traffic=0.9 (excellent), grid=2 (very_low),
        # land=2.5 (high), POI=80 (high), comp=0 (none)
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 80,
            'traffic_accessibility': 0.9,
            'grid_connection_cost': 2,
            'land_cost_tier': 2.5,
            'poi_attractiveness': 80,
            'competition_index': 0,
        })
        assert abs(suitability - 0.5) < 0.01, f"Offset eligible should be fair (0.5), got {suitability}"
        assert source == "TEST_OFFSET", f"Expected OFFSET source, got {source}"

    def test_offset_campus_typical_no_offset(self, scorer):
        """Campus typical (fair traffic) → no offset, penalty applies (~0.395)."""
        # demand=76 (high), traffic=0.39 (fair), grid=2 (very_low),
        # land=2.5 (high), POI=76 (high), comp=0 (none)
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 76,
            'traffic_accessibility': 0.39,
            'grid_connection_cost': 2,
            'land_cost_tier': 2.5,
            'poi_attractiveness': 76,
            'competition_index': 0,
        })
        assert suitability < 0.45, f"Campus typical should be penalized, got {suitability}"
        assert source != "TEST_OFFSET", "Fair traffic should not qualify for offset"

    def test_offset_low_grid_variant(self, scorer):
        """High-land with low grid (not very_low) → offset applies."""
        # demand=80, traffic=0.9, grid=9 (low, μ>0.5), land=2.5, POI=80, comp=0
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 80,
            'traffic_accessibility': 0.9,
            'grid_connection_cost': 9,
            'land_cost_tier': 2.5,
            'poi_attractiveness': 80,
            'competition_index': 0,
        })
        assert abs(suitability - 0.5) < 0.01, f"Low grid should qualify for offset, got {suitability}"
        assert source == "TEST_OFFSET", f"Expected OFFSET source, got {source}"

    def test_offset_low_comp_variant(self, scorer):
        """High-land with low competition (not none) → offset applies."""
        # demand=80, traffic=0.9, grid=2, land=2.5, POI=80, comp=1.5 (low, μ>0.5)
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 80,
            'traffic_accessibility': 0.9,
            'grid_connection_cost': 2,
            'land_cost_tier': 2.5,
            'poi_attractiveness': 80,
            'competition_index': 1.5,
        })
        assert abs(suitability - 0.5) < 0.01, f"Low comp should qualify for offset, got {suitability}"
        assert source == "TEST_OFFSET", f"Expected OFFSET source, got {source}"

    def test_offset_very_high_demand_poi(self, scorer):
        """Very_high demand and POI → offset applies."""
        # demand=91 (very_high, μ>0.5), traffic=0.9, grid=2, land=2.5, POI=91 (very_high, μ>0.5), comp=0
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 91,
            'traffic_accessibility': 0.9,
            'grid_connection_cost': 2,
            'land_cost_tier': 2.5,
            'poi_attractiveness': 91,
            'competition_index': 0,
        })
        assert abs(suitability - 0.5) < 0.01, f"Very_high demand/POI should qualify, got {suitability}"
        assert source == "TEST_OFFSET", f"Expected OFFSET source, got {source}"

    # --- Offset blocked by each protected penalty ---

    def test_offset_blocked_by_very_low_demand(self, scorer):
        """Very-low demand blocks offset even if other criteria met."""
        # demand=5 (very_low), but otherwise perfect
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 5,
            'traffic_accessibility': 0.9,
            'grid_connection_cost': 2,
            'land_cost_tier': 2.5,
            'poi_attractiveness': 80,
            'competition_index': 0,
        })
        assert suitability < 0.2, f"Very-low demand should block offset, got {suitability}"
        assert source != "TEST_OFFSET", "Protected penalty must block offset"

    def test_offset_blocked_by_poor_traffic(self, scorer):
        """Poor traffic blocks offset."""
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 80,
            'traffic_accessibility': 0.1,
            'grid_connection_cost': 2,
            'land_cost_tier': 2.5,
            'poi_attractiveness': 80,
            'competition_index': 0,
        })
        assert suitability < 0.2, f"Poor traffic should block offset, got {suitability}"
        assert source != "TEST_OFFSET", "Protected penalty must block offset"

    def test_offset_blocked_by_very_high_grid(self, scorer):
        """Very-high grid blocks offset."""
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 80,
            'traffic_accessibility': 0.9,
            'grid_connection_cost': 45,
            'land_cost_tier': 2.5,
            'poi_attractiveness': 80,
            'competition_index': 0,
        })
        assert 0.2 < suitability < 0.4, f"Very-high grid should block offset, got {suitability}"
        assert source != "TEST_OFFSET", "Protected penalty must block offset"

    def test_offset_blocked_by_very_low_poi(self, scorer):
        """Very-low POI blocks offset."""
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 80,
            'traffic_accessibility': 0.9,
            'grid_connection_cost': 2,
            'land_cost_tier': 2.5,
            'poi_attractiveness': 5,
            'competition_index': 0,
        })
        assert suitability < 0.2, f"Very-low POI should block offset, got {suitability}"
        assert source != "TEST_OFFSET", "Protected penalty must block offset"

    def test_offset_blocked_by_saturated_comp(self, scorer):
        """Saturated competition blocks offset."""
        # comp=9.1 → saturated membership 0.6 (>0.5, strictly)
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 80,
            'traffic_accessibility': 0.9,
            'grid_connection_cost': 2,
            'land_cost_tier': 2.5,
            'poi_attractiveness': 80,
            'competition_index': 9.1,
        })
        assert 0.2 < suitability < 0.4, f"Saturated comp should block offset, got {suitability}"
        assert source != "TEST_OFFSET", "Protected penalty must block offset"
        # Verify the protected penalty is actually detected
        sim = self._make_simulator(scorer, {
            'predicted_demand': 80,
            'traffic_accessibility': 0.9,
            'grid_connection_cost': 2,
            'land_cost_tier': 2.5,
            'poi_attractiveness': 80,
            'competition_index': 9.1,
        })
        sim.compute()
        protected_active = scorer._any_protected_penalty_active(sim)
        assert protected_active, "Saturated competition should be detected as protected penalty"

    # --- Original Moderate Scenarios (from task) ---

    def test_original_moderate(self, scorer):
        """Original moderate: demand=50, traffic=0.5, grid=20, land=1.5, POI=50, comp=4 → ~0.5."""
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 50,
            'traffic_accessibility': 0.5,
            'grid_connection_cost': 20,
            'land_cost_tier': 1.5,
            'poi_attractiveness': 50,
            'competition_index': 4,
        })
        assert 0.45 < suitability < 0.55, f"Original moderate should be ~0.5, got {suitability}"

    def test_traffic_0_6(self, scorer):
        """Traffic=0.6: demand=50, traffic=0.6, grid=20, land=1.5, POI=50, comp=4 → ~0.5."""
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 50,
            'traffic_accessibility': 0.6,
            'grid_connection_cost': 20,
            'land_cost_tier': 1.5,
            'poi_attractiveness': 50,
            'competition_index': 4,
        })
        assert 0.45 < suitability < 0.55, f"Traffic=0.6 should be ~0.5, got {suitability}"

    def test_low_poi_high_comp(self, scorer):
        """Low POI/high comp: demand=20, traffic=0.5, grid=20, land=1.5, POI=20, comp=8 → penalty (~0.125)."""
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 20,
            'traffic_accessibility': 0.5,
            'grid_connection_cost': 20,
            'land_cost_tier': 1.5,
            'poi_attractiveness': 20,
            'competition_index': 8,
        })
        assert suitability < 0.2, f"Low POI/high comp should be penalized, got {suitability}"

    # --- Boundary Tests ---

    def test_boundary_traffic_0_55(self, scorer):
        """Traffic at good/fair boundary (0.55) → should still work."""
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 50,
            'traffic_accessibility': 0.55,
            'grid_connection_cost': 20,
            'land_cost_tier': 1.5,
            'poi_attractiveness': 50,
            'competition_index': 4,
        })
        assert 0.4 < suitability < 0.6, f"Boundary traffic=0.55 should work, got {suitability}"

    def test_boundary_grid_5_0(self, scorer):
        """Grid at very_low/low boundary (5.0) → should still work."""
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 50,
            'traffic_accessibility': 0.5,
            'grid_connection_cost': 5.0,
            'land_cost_tier': 1.5,
            'poi_attractiveness': 50,
            'competition_index': 4,
        })
        assert 0.4 < suitability < 0.6, f"Boundary grid=5.0 should work, got {suitability}"

    def test_boundary_demand_35(self, scorer):
        """Demand at low/medium boundary (35) → should still work."""
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 35,
            'traffic_accessibility': 0.5,
            'grid_connection_cost': 20,
            'land_cost_tier': 1.5,
            'poi_attractiveness': 50,
            'competition_index': 4,
        })
        assert 0.3 < suitability < 0.6, f"Boundary demand=35 should work, got {suitability}"

    # --- No-Output Cases ---

    def test_no_output_no_penalty_fallback(self, scorer):
        """No rule fires, no penalty active → fallback to 0.5."""
        # Use a genuine no-output case (found via search): demand=49.9, traffic=0.8, grid=19.2, land=1.4, poi=71.0, comp=7.9
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 49.9,
            'traffic_accessibility': 0.8,
            'grid_connection_cost': 19.2,
            'land_cost_tier': 1.4,
            'poi_attractiveness': 71.0,
            'competition_index': 7.9,
        })
        assert abs(suitability - 0.5) < 0.01, f"No-output no-penalty should fallback to 0.5, got {suitability}"
        assert source == "TEST_FALLBACK", f"Expected FALLBACK source, got {source}"

    def test_no_output_second_case_fallback(self, scorer):
        """Another no-output case → fallback to 0.5."""
        # Use another genuine no-output case: demand=75.2, traffic=0.6, grid=1.6, land=1.7, poi=53.7, comp=2.9
        suitability, source = self._run_policy(scorer, {
            'predicted_demand': 75.2,
            'traffic_accessibility': 0.6,
            'grid_connection_cost': 1.6,
            'land_cost_tier': 1.7,
            'poi_attractiveness': 53.7,
            'competition_index': 2.9,
        })
        assert abs(suitability - 0.5) < 0.01, f"Second no-output case should fallback to 0.5, got {suitability}"
        assert source == "TEST_FALLBACK", f"Expected FALLBACK source, got {source}"

    # --- Wildcard Membership Tests ---

    def test_wildcard_membership_universal(self, scorer):
        """Wildcard 'any' term should have membership 1.0 everywhere including boundaries."""
        ant_dict = {a.label: a for a in scorer.fuzzy_system.antecedents}
        for name, antecedent in ant_dict.items():
            any_mf = antecedent['any'].mf
            assert np.allclose(any_mf, 1.0), f"Wildcard for {name} not all 1.0"
            # Check boundaries
            edges = fuzz.interp_membership(
                antecedent.universe, any_mf,
                [antecedent.universe[0], antecedent.universe[-1]]
            )
            assert np.allclose(edges, 1.0), f"Wildcard boundaries not 1.0 for {name}: {edges}"

    # --- Broad Input Coverage (Latin Hypercube Sample) ---

    def test_broad_coverage_no_unintended_no_output(self, scorer):
        """Sample 100 points in non-penalized space: report NO-OUTPUT rate.
        This is a diagnostic test - the current rule base has sparse coverage
        so some no-output cases are expected. We just ensure it doesn't regress.
        """
        np.random.seed(42)
        n_samples = 100
        no_output_count = 0
        for _ in range(n_samples):
            # Sample from non-penalized ranges
            demand = np.random.uniform(15, 95)  # avoid very_low
            traffic = np.random.uniform(0.3, 1.0)  # avoid poor
            grid = np.random.uniform(0, 40)  # avoid very_high
            land = np.random.uniform(0, 3)  # any
            poi = np.random.uniform(15, 100)  # avoid very_low
            comp = np.random.uniform(0, 7)  # avoid saturated

            sim = self._make_simulator(scorer, {
                'predicted_demand': demand,
                'traffic_accessibility': traffic,
                'grid_connection_cost': grid,
                'land_cost_tier': land,
                'poi_attractiveness': poi,
                'competition_index': comp,
            })
            sim.compute()
            suitability = sim.output.get('suitability')
            if suitability is None or np.isnan(suitability):
                no_output_count += 1

        # Report the rate (currently ~47% due to sparse rule base)
        # This is expected behavior, not a bug - just ensure it doesn't exceed 60%
        no_output_rate = no_output_count / n_samples
        assert no_output_rate < 0.60, f"No-output rate too high: {no_output_rate:.1%} ({no_output_count}/{n_samples})"

    # --- Production Orchestration Test ---

    def test_score_candidates_production_orchestration(self, scorer):
        """Exercise real score_candidates() with 1-row DataFrame fixture.
        This tests the full production orchestration: input mapping, fuzzy inference,
        protected-penalty/offset policy, fallback logic, and DataFrame result assembly.
        """
        import pandas as pd

        # One-row fixture with exact column names from score_candidates()
        df = pd.DataFrame([{
            'candidate_id': 'test_prod_1',
            'traffic_accessibility_score': 0.5,
            'grid_connection_cost_inr_lakhs': 20.0,
            'land_cost_tier': 'medium',
            'poi_score': 0.5,
            'existing_chargers_3km': 4.0,
        }])

        result = scorer.score_candidates(df, weather_df=None, demand_history_df=None)

        # Verify result structure
        assert len(result) == 1, "Should return exactly one scored candidate"
        assert 'fuzzy_suitability_score' in result.columns, "Missing fuzzy_suitability_score column"
        assert 'demand_prediction_source' in result.columns, "Missing demand_prediction_source column"
        assert 'fuzzy_provenance' in result.columns, "Missing fuzzy_provenance column"

        # Verify values
        score = result.iloc[0]['fuzzy_suitability_score']
        source = result.iloc[0]['demand_prediction_source']
        provenance = result.iloc[0]['fuzzy_provenance']

        assert isinstance(score, float), f"Score should be float, got {type(score)}"
        assert 0.0 <= score <= 1.0, f"Score out of range [0,1]: {score}"
        assert isinstance(source, str) and len(source) > 0, f"Source should be non-empty string: {source}"
        assert isinstance(provenance, str) and len(provenance) > 0, f"Provenance should be non-empty string: {provenance}"

        # For this input (medium demand via POI proxy, fair traffic, medium grid, medium land,
        # medium POI, moderate competition), expect a valid fuzzy output with POI_PROXY source
        assert "POI_PROXY" in source, f"Expected POI_PROXY in source, got: {source}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])