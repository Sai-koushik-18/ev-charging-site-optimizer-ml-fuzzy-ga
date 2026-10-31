"""
Genetic Algorithm Optimizer for EV Charging Station Placement
Implements binary-encoded GA with fixed cardinality constraint for site selection.

Provenance: SYNTHETIC (custom implementation based on DEAP principles)
"""

import numpy as np
import pandas as pd
import yaml
import json
import logging
import random
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass
import itertools

logger = logging.getLogger(__name__)


@dataclass
class Individual:
    """Represents a GA individual (chromosome)."""
    chromosome: np.ndarray  # Binary array representing selected/deselected candidates
    fitness: float = 0.0
    fitness_components: Dict[str, float] = None

    def is_feasible(self) -> bool:
        """Check if individual represents a feasible solution."""
        if self.fitness_components is None:
            return False
        # Feasible: fitness > 0, no budget violation, no unknown costs
        return (self.fitness > 0.0 and
                self.fitness_components.get('budget_violation', 1.0) == 0.0 and
                self.fitness_components.get('unknown_cost', True) is False)


class GeneticAlgorithmOptimizer:
    """
    Genetic Algorithm for optimizing EV charging station placement.
    Uses binary encoding with fixed cardinality constraint.
    """

    def __init__(self, config_path: str = "config/ga_config.yaml"):
        """
        Initialize the GA optimizer.

        Args:
            config_path: Path to GA configuration YAML file
        """
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.candidates_df = None
        self.distance_matrix = None
        self._validate_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load GA configuration from YAML file."""
        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            logger.info(f"Loaded GA config from {self.config_path}")
            return config['ga']
        except Exception as e:
            logger.error(f"Failed to load GA config: {e}")
            raise

    def _validate_config(self):
        """Validate configuration parameters."""
        required_keys = ['population_size', 'max_generations', 'n_stations_target']
        for key in required_keys:
            if key not in self.config:
                raise ValueError(f"Missing required config key: {key}")

        logger.info("GA configuration validated")

    def load_candidates(self, candidates_csv_path: str = "data/processed/candidates_fuzzy_scored.csv"):
        """
        Load candidate data and precompute distance matrix.

        Args:
            candidates_csv_path: Path to candidates CSV file
        """
        try:
            self.candidates_df = pd.read_csv(candidates_csv_path)
            # Update n_candidates from actual data
            self.config['n_candidates'] = len(self.candidates_df)
            logger.info(f"Loaded {len(self.candidates_df)} candidates from {candidates_csv_path}")

            # Precompute distance matrix for spatial calculations
            self._compute_distance_matrix()

        except Exception as e:
            logger.error(f"Failed to load candidates: {e}")
            raise

    def _compute_distance_matrix(self):
        """Precompute distance matrix between all candidates for spatial calculations.
        Uses Haversine formula for geographic distance in meters.
        """
        if self.candidates_df is None:
            raise ValueError("Candidates not loaded. Call load_candidates() first.")

        try:
            n = len(self.candidates_df)
            self.distance_matrix = np.zeros((n, n))

            # Use Haversine formula for geographic distances
            coords = self.candidates_df[['longitude', 'latitude']].values  # [lon, lat]
            lat = np.radians(coords[:, 1])
            lon = np.radians(coords[:, 0])
            R = 6371000  # Earth radius in meters

            for i in range(n):
                # Vectorized haversine for row i
                dlat = lat - lat[i]
                dlon = lon - lon[i]
                a = np.sin(dlat/2)**2 + np.cos(lat[i]) * np.cos(lat) * np.sin(dlon/2)**2
                c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
                self.distance_matrix[i, :] = R * c

            # Ensure diagonal is zero
            np.fill_diagonal(self.distance_matrix, 0)

            # Make symmetric (should already be, but ensure)
            self.distance_matrix = (self.distance_matrix + self.distance_matrix.T) / 2

            logger.info(f"Computed geographic distance matrix for {n} candidates (Haversine, meters)")

        except Exception as e:
            logger.error(f"Failed to compute distance matrix: {e}")
            # Fallback: use simple Euclidean approximation
            n = len(self.candidates_df)
            self.distance_matrix = np.ones((n, n)) * 500  # 500m default distance
            np.fill_diagonal(self.distance_matrix, 0)

    def _create_random_valid_individual(self) -> Individual:
        """
        Create a random individual that satisfies the cardinality constraint.

        Returns:
            Individual with exactly n_stations_target genes set to 1
        """
        chromosome = np.zeros(self.config['n_candidates'], dtype=int)
        # Randomly select exactly n_stations_target positions to be 1
        selected_indices = random.sample(range(self.config['n_candidates']),
                                       self.config['n_stations_target'])
        chromosome[selected_indices] = 1

        return Individual(chromosome=chromosome)

    def _initialize_population(self) -> List[Individual]:
        """
        Initialize population with random valid individuals.

        Returns:
            List of Individuals forming the initial population
        """
        population = []
        for _ in range(self.config['population_size']):
            individual = self._create_random_valid_individual()
            population.append(individual)

        logger.info(f"Initialized population of size {len(population)}")
        return population

    def _calculate_fitness(self, individual: Individual) -> Tuple[float, Dict[str, float]]:
        """
        Calculate fitness of an individual based on multiple objectives.
        HARD BUDGET CONSTRAINT: individuals exceeding budget get fitness = 0.

        Args:
            individual: Individual to evaluate

        Returns:
            Tuple of (fitness_score, fitness_components_dict)
        """
        if self.candidates_df is None:
            raise ValueError("Candidates not loaded. Call load_candidates() first.")

        # Get selected candidates
        selected_mask = individual.chromosome == 1
        selected_indices = np.where(selected_mask)[0]

        if len(selected_indices) == 0:
            return 0.0, {"demand_coverage": 0.0, "spatial_coverage": 0.0,
                        "cost_efficiency": 0.0, "redundancy_penalty": 0.0,
                        "budget_violation": 1.0,
                        "total_cost_lakhs": 0.0,
                        "unknown_cost": False}

        selected_df = self.candidates_df.iloc[selected_indices]

        # Check for UNKNOWN total_capex (NaN) - candidates with unknown grid costs are infeasible
        if selected_df['total_capex_inr_lakhs'].isna().any():
            logger.debug("Individual contains candidate(s) with UNKNOWN total_capex - infeasible")
            return 0.0, {
                "demand_coverage": 0.0,
                "spatial_coverage": 0.0,
                "cost_efficiency": 0.0,
                "redundancy_penalty": 1.0,
                "budget_violation": 1.0,
                "total_cost_lakhs": np.nan,
                "unknown_cost": True
            }

        # HARD BUDGET CONSTRAINT CHECK
        total_cost = selected_df['total_capex_inr_lakhs'].sum()
        budget_lakhs = self.config['fitness_params']['budget_lakhs']

        if total_cost > budget_lakhs:
            # Hard constraint violation - zero fitness
            logger.debug(f"Individual exceeds budget: {total_cost:.1f} > {budget_lakhs:.1f} lakhs")
            return 0.0, {
                "demand_coverage": 0.0,
                "spatial_coverage": 0.0,
                "cost_efficiency": 0.0,
                "redundancy_penalty": 1.0,
                "budget_violation": 1.0,
                "total_cost_lakhs": total_cost
            }

        # HARD MIN-SEPARATION CONSTRAINT CHECK
        if self._violates_min_separation(selected_indices):
            logger.debug("Individual has stations closer than min_station_distance_m - infeasible")
            return 0.0, {
                "demand_coverage": 0.0,
                "spatial_coverage": 0.0,
                "cost_efficiency": 0.0,
                "redundancy_penalty": 1.0,
                "budget_violation": 0.0,
                "separation_violation": 1.0,
                "total_cost_lakhs": total_cost,
                "unknown_cost": False
            }

        # 1. Demand Coverage: Sum of fuzzy suitability scores of selected stations
        demand_coverage = selected_df['fuzzy_suitability_score'].sum() if 'fuzzy_suitability_score' in selected_df.columns else len(selected_indices)
        # Normalize by max possible (if all candidates selected)
        max_demand_coverage = self.candidates_df['fuzzy_suitability_score'].sum() if 'fuzzy_suitability_score' in self.candidates_df.columns else self.config['n_candidates']
        demand_coverage_normalized = demand_coverage / max(max_demand_coverage, 1)

        # 2. Spatial Coverage: Percentage of candidates within coverage radius of any selected station
        spatial_coverage = self._calculate_spatial_coverage(selected_indices)

        # 3. Cost Efficiency: Inverse of normalized total cost (lower cost = higher efficiency)
        # Only consider candidates with known costs for max_possible_cost
        valid_costs = self.candidates_df['total_capex_inr_lakhs'].dropna()
        max_possible_cost = valid_costs.sum() if not valid_costs.empty else 1.0
        cost_efficiency = 1.0 - (total_cost / max(max_possible_cost, 1.0))

        # 4. Redundancy Penalty: Based on minimum distance between stations
        redundancy_penalty = self._calculate_redundancy_penalty(selected_indices)

        # Weighted fitness combination
        weights = self.config['fitness_weights']
        fitness = (weights['demand_coverage'] * demand_coverage_normalized +
                  weights['spatial_coverage'] * spatial_coverage +
                  weights['cost_efficiency'] * cost_efficiency -
                  weights['redundancy_penalty'] * redundancy_penalty)

        # Ensure fitness is between 0 and 1
        fitness = max(0.0, min(1.0, fitness))

        fitness_components = {
            "demand_coverage": demand_coverage_normalized,
            "spatial_coverage": spatial_coverage,
            "cost_efficiency": cost_efficiency,
            "redundancy_penalty": redundancy_penalty,
            "budget_violation": 0.0,
            "total_cost_lakhs": total_cost,
            "unknown_cost": False
        }

        return fitness, fitness_components

    def _calculate_spatial_coverage(self, selected_indices: np.ndarray) -> float:
        """
        Calculate what percentage of candidates are within coverage radius of selected stations.

        Args:
            selected_indices: Indices of selected candidates

        Returns:
            Spatial coverage ratio (0-1)
        """
        if len(selected_indices) == 0 or self.distance_matrix is None:
            return 0.0

        coverage_radius = self.config['fitness_params']['coverage_radius_m']
        n_candidates = len(self.candidates_df)

        # For each candidate, check if it's within coverage radius of any selected station
        covered_count = 0
        for i in range(n_candidates):
            # Check distance to nearest selected station
            if len(selected_indices) > 0:
                min_distance = np.min(self.distance_matrix[i, selected_indices])
                if min_distance <= coverage_radius:
                    covered_count += 1

        return covered_count / n_candidates

    def _violates_min_separation(self, selected_indices: np.ndarray) -> bool:
        """
        Check whether any pair of selected stations is closer than min_station_distance_m.

        Args:
            selected_indices: Indices of selected candidates

        Returns:
            True if the hard separation constraint is violated
        """
        if len(selected_indices) < 2 or self.distance_matrix is None:
            return False

        min_distance = self.config['fitness_params']['min_station_distance_m']
        pairwise = self.distance_matrix[np.ix_(selected_indices, selected_indices)]
        upper = pairwise[np.triu_indices(len(selected_indices), k=1)]
        return bool(np.any(upper < min_distance))

    def _calculate_redundancy_penalty(self, selected_indices: np.ndarray) -> float:
        """
        Calculate penalty for stations being too close to each other.

        Args:
            selected_indices: Indices of selected candidates

        Returns:
            Redundancy penalty (0-1, higher means more penalty)
        """
        if len(selected_indices) < 2:
            return 0.0

        min_distance = self.config['fitness_params']['min_station_distance_m']
        penalties = []

        # Check pairwise distances between selected stations
        for i in range(len(selected_indices)):
            for j in range(i + 1, len(selected_indices)):
                idx1, idx2 = selected_indices[i], selected_indices[j]
                distance = self.distance_matrix[idx1, idx2]
                if distance < min_distance:
                    # Penalty increases as stations get closer
                    penalty = 1.0 - (distance / min_distance)
                    penalties.append(penalty)

        if not penalties:
            return 0.0

        # Average penalty
        return np.mean(penalties)

    def _selection_tournament(self, population: List[Individual]) -> Individual:
        """
        Tournament selection.

        Args:
            population: Current population

        Returns:
            Selected individual
        """
        tournament_size = self.config['selection']['tournament_size']
        tournament = random.sample(population, min(tournament_size, len(population)))
        # Return individual with highest fitness
        return max(tournament, key=lambda ind: ind.fitness)

    def _crossover_uniform(self, parent1: Individual, parent2: Individual) -> Tuple[Individual, Individual]:
        """
        Uniform crossover that preserves cardinality constraint.

        Args:
            parent1, parent2: Parent individuals

        Returns:
            Tuple of two offspring individuals
        """
        if random.random() > self.config['crossover']['probability']:
            # No crossover, return copies of parents
            return Individual(chromosome=parent1.chromosome.copy()), \
                   Individual(chromosome=parent2.chromosome.copy())

        # Create offspring chromosomes
        chrom1 = parent1.chromosome.copy()
        chrom2 = parent2.chromosome.copy()

        # Uniform crossover with independent bit swapping
        indpb = self.config['crossover']['indpb']
        for i in range(len(chrom1)):
            if random.random() < indpb:
                chrom1[i], chrom2[i] = chrom2[i], chrom1[i]

        # Repair to maintain cardinality constraint
        offspring1 = self._repair_cardinality(Individual(chromosome=chrom1))
        offspring2 = self._repair_cardinality(Individual(chromosome=chrom2))

        return offspring1, offspring2

    def _mutation_bit_flip(self, individual: Individual) -> Individual:
        """
        Bit flip mutation that preserves cardinality constraint.

        Args:
            individual: Individual to mutate

        Returns:
            Mutated individual
        """
        if random.random() > self.config['mutation']['probability']:
            # No mutation
            return individual

        # Create copy of chromosome
        chromosome = individual.chromosome.copy()

        # Apply bit flip mutation with independent probability
        indpb = self.config['mutation']['indpb']
        for i in range(len(chromosome)):
            if random.random() < indpb:
                chromosome[i] = 1 - chromosome[i]  # Flip bit

        # Repair to maintain cardinality constraint
        mutated = self._repair_cardinality(Individual(chromosome=chromosome))

        return mutated

    def _repair_cardinality(self, individual: Individual) -> Individual:
        """
        Repair individual to satisfy fixed cardinality constraint.

        Args:
            individual: Individual to repair

        Returns:
            Repaired individual with exactly n_stations_target genes set to 1
        """
        current_count = np.sum(individual.chromosome)
        target_count = self.config['n_stations_target']

        if current_count == target_count:
            # Already satisfies constraint
            return individual
        elif current_count > target_count:
            # Too many 1s, randomly turn some to 0
            ones_indices = np.where(individual.chromosome == 1)[0]
            indices_to_zero = random.sample(list(ones_indices), current_count - target_count)
            individual.chromosome[indices_to_zero] = 0
        else:
            # Too few 1s, randomly turn some 0s to 1
            zeros_indices = np.where(individual.chromosome == 0)[0]
            indices_to_one = random.sample(list(zeros_indices), target_count - current_count)
            individual.chromosome[indices_to_one] = 1

        # Reset fitness since chromosome changed
        individual.fitness = 0.0
        individual.fitness_components = None

        return individual

    def optimize(self) -> Tuple[Individual, List[float], bool]:
        """
        Run the genetic algorithm optimization.

        Returns:
            Tuple of (best_individual, fitness_history, feasible_found)
            best_individual is the best FEASIBLE individual if any was found,
            otherwise the best overall (infeasible) individual.
            feasible_found is True iff the returned best_individual is feasible.
        """
        logger.info("Starting GA optimization...")

        # Initialize population
        population = self._initialize_population()

        # Evaluate initial population
        for individual in population:
            individual.fitness, individual.fitness_components = self._calculate_fitness(individual)

        # Track best individual overall (for logging/history)
        best_overall = max(population, key=lambda ind: ind.fitness)
        # Track best FEASIBLE individual separately (copied, cannot be lost)
        best_feasible: Optional[Individual] = None
        for ind in population:
            if ind.is_feasible():
                if best_feasible is None or ind.fitness > best_feasible.fitness:
                    best_feasible = Individual(
                        chromosome=ind.chromosome.copy(),
                        fitness=ind.fitness,
                        fitness_components=ind.fitness_components.copy() if ind.fitness_components else None
                    )

        fitness_history = [best_overall.fitness]
        feasible_found = best_feasible is not None

        logger.info(f"Initial best fitness: {best_overall.fitness:.4f} (feasible: {feasible_found})")

        # Evolution loop
        for generation in range(self.config['max_generations']):
            # Selection
            selected = []
            for _ in range(self.config['population_size']):
                selected.append(self._selection_tournament(population))

            # Crossover and Mutation
            next_population = []
            for i in range(0, len(selected), 2):
                if i + 1 < len(selected):
                    # Create offspring from pair
                    offspring1, offspring2 = self._crossover_uniform(selected[i], selected[i + 1])
                    # Apply mutation
                    offspring1 = self._mutation_bit_flip(offspring1)
                    offspring2 = self._mutation_bit_flip(offspring2)
                    next_population.extend([offspring1, offspring2])
                else:
                    # Odd population size - keep last individual
                    next_population.append(selected[i])

            # Evaluate new population
            for individual in next_population:
                individual.fitness, individual.fitness_components = self._calculate_fitness(individual)

            # Track best feasible individual in new population
            for individual in next_population:
                if individual.is_feasible():
                    if best_feasible is None or individual.fitness > best_feasible.fitness:
                        best_feasible = Individual(
                            chromosome=individual.chromosome.copy(),
                            fitness=individual.fitness,
                            fitness_components=individual.fitness_components.copy() if individual.fitness_components else None
                        )

            # Elitism: keep best individuals from previous generation
            elite_count = min(self.config['elitism_count'], len(population))
            elite = sorted(population, key=lambda ind: ind.fitness, reverse=True)[:elite_count]

            # Combine elite with new population and select best
            combined = elite + next_population
            population = sorted(combined, key=lambda ind: ind.fitness, reverse=True)[:self.config['population_size']]

            # Update best overall (for fitness history tracking)
            current_best = max(population, key=lambda ind: ind.fitness)
            if current_best.fitness > best_overall.fitness:
                best_overall = Individual(
                    chromosome=current_best.chromosome.copy(),
                    fitness=current_best.fitness,
                    fitness_components=current_best.fitness_components.copy() if current_best.fitness_components else None
                )

            fitness_history.append(best_overall.fitness)

            # Logging
            if generation % self.config['logging']['log_every_n_generations'] == 0:
                bf_fit = f"{best_feasible.fitness:.4f}" if best_feasible is not None else "none"
                logger.info(f"Generation {generation:3d} | Best Fitness: {best_overall.fitness:.4f} | "
                           f"Avg Fitness: {np.mean([ind.fitness for ind in population]):.4f} | "
                           f"Best feasible: {bf_fit}")

            # Early stopping check
            if self.config['early_stopping']['enabled'] and generation >= self.config['early_stopping']['patience']:
                recent_improvement = max(fitness_history[-self.config['early_stopping']['patience']:]) - \
                                   min(fitness_history[-self.config['early_stopping']['patience']:])
                min_delta = float(self.config['early_stopping']['min_delta'])
                if recent_improvement < min_delta:
                    logger.info(f"Early stopping triggered at generation {generation}")
                    break

        # FINAL SELECTION: prefer best feasible over best overall
        if best_feasible is not None:
            best_individual = best_feasible
            feasible_found = True
            logger.info(f"Optimization completed. Returning BEST FEASIBLE individual. Fitness: {best_individual.fitness:.4f}")
        else:
            best_individual = best_overall
            feasible_found = False
            logger.info(f"Optimization completed. NO FEASIBLE SOLUTION found. Best infeasible fitness: {best_individual.fitness:.4f}")

        logger.info(f"Selected stations: {np.sum(best_individual.chromosome)}")
        logger.info(f"Feasible solution returned: {feasible_found}")

        return best_individual, fitness_history, feasible_found

    def get_solution_details(self, individual: Individual) -> Tuple[pd.DataFrame, bool]:
        """
        Get detailed information about the selected solution.
        Performs independent validation of the selected candidates.

        Args:
            individual: Optimized individual

        Returns:
            Tuple of (DataFrame with selected candidates, is_feasible)
        """
        if self.candidates_df is None:
            raise ValueError("Candidates not loaded. Call load_candidates() first.")

        # First check individual's own feasibility
        is_feasible = individual.is_feasible()

        if not is_feasible:
            # Return empty DataFrame with NO FEASIBLE SOLUTION marker
            empty_df = pd.DataFrame()
            empty_df.attrs['feasibility_status'] = 'NO FEASIBLE SOLUTION'
            return empty_df, False

        # INDEPENDENT VALIDATION of selected candidates
        selected_mask = individual.chromosome == 1
        selected_indices = np.where(selected_mask)[0]

        # 1. Check selection non-empty
        if len(selected_indices) == 0:
            logger.warning("Independent validation failed: empty selection")
            empty_df = pd.DataFrame()
            empty_df.attrs['feasibility_status'] = 'NO FEASIBLE SOLUTION'
            return empty_df, False

        selected_df = self.candidates_df.iloc[selected_indices].copy()

        # 2. Check all total_capex_inr_lakhs are finite (not NaN, not +/-inf, numeric)
        capex_series = pd.to_numeric(selected_df['total_capex_inr_lakhs'], errors='coerce')
        if not np.all(np.isfinite(capex_series.values)):
            logger.warning("Independent validation failed: non-finite total_capex (NaN or +/-inf) in selected candidates")
            empty_df = pd.DataFrame()
            empty_df.attrs['feasibility_status'] = 'NO FEASIBLE SOLUTION'
            return empty_df, False

        # 3. Check total CAPEX <= budget
        total_cost = float(capex_series.sum())
        budget_lakhs = self.config['fitness_params']['budget_lakhs']
        if not np.isfinite(total_cost) or total_cost > budget_lakhs:
            logger.warning(f"Independent validation failed: total CAPEX {total_cost:.2f} > budget {budget_lakhs:.2f} or non-finite")
            empty_df = pd.DataFrame()
            empty_df.attrs['feasibility_status'] = 'NO FEASIBLE SOLUTION'
            return empty_df, False

        # 4. Check minimum separation between selected stations
        if self._violates_min_separation(selected_indices):
            logger.warning("Independent validation failed: stations closer than min_station_distance_m")
            empty_df = pd.DataFrame()
            empty_df.attrs['feasibility_status'] = 'NO FEASIBLE SOLUTION'
            return empty_df, False

        # All independent validations passed
        selected_df['selected_in_ga'] = True
        selected_df['ga_fitness_component'] = individual.fitness

        # Add rank based on fitness contribution
        if len(selected_df) > 0:
            selected_df = selected_df.sort_values('fuzzy_suitability_score', ascending=False)
            selected_df['ga_rank'] = range(1, len(selected_df) + 1)

        selected_df.attrs['feasibility_status'] = 'FEASIBLE'
        return selected_df, True


def main():
    """Main function to run GA optimization."""
    import argparse

    parser = argparse.ArgumentParser(description='Run GA optimization for EV charging station placement')
    parser.add_argument('--candidates', '-c', default='data/processed/candidates_fuzzy_scored.csv',
                        help='Input candidates CSV file (should be fuzzy scored)')
    parser.add_argument('--config', '-g', default='config/ga_config.yaml',
                        help='GA configuration file')
    parser.add_argument('--output', '-o', default='data/processed/ga_solution.csv',
                        help='Output solution CSV file')
    parser.add_argument('--log-level', default='INFO',
                        help='Logging level')

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s'
    )

    try:
        # Initialize GA optimizer
        ga = GeneticAlgorithmOptimizer(args.config)

        # Load candidates
        ga.load_candidates(args.candidates)

        # Run optimization
        best_individual, fitness_history, feasible_found = ga.optimize()

        # Get solution details
        solution_df, is_feasible = ga.get_solution_details(best_individual)

        if not is_feasible:
            # NO FEASIBLE SOLUTION - log and exit without saving invalid recommendation
            print("\n=== GA Optimization Results ===")
            print(f"Generations completed: {len(fitness_history)}")
            print(f"Best fitness: {best_individual.fitness:.4f}")
            print(f"Status: NO FEASIBLE SOLUTION")
            print(f"Budget: {ga.config['fitness_params']['budget_lakhs']:.1f} INR Lakhs")
            if best_individual.fitness_components:
                print(f"Total CAPEX (best individual): {best_individual.fitness_components.get('total_cost_lakhs', 'N/A')}")
                if best_individual.fitness_components.get('budget_violation', 0) == 1:
                    print("  -> Budget violation")
                if best_individual.fitness_components.get('unknown_cost', False):
                    print("  -> Unknown grid connection costs")
                if best_individual.fitness_components.get('separation_violation', 0) == 1:
                    print("  -> Minimum station separation violated")
            logger.warning("NO FEASIBLE SOLUTION found within budget and cost constraints")
            return 1

        # Save solution
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        solution_df.to_csv(args.output, index=False)
        logger.info(f"Saved GA solution to {args.output}")

        # Print summary
        print("\n=== GA Optimization Results ===")
        print(f"Generations completed: {len(fitness_history)}")
        print(f"Best fitness: {best_individual.fitness:.4f}")
        print(f"Stations selected: {np.sum(best_individual.chromosome)} / {ga.config['n_stations_target']}")
        print(f"Total CAPEX: {solution_df['total_capex_inr_lakhs'].sum():.2f} INR Lakhs")
        print(f"Average suitability: {solution_df['fuzzy_suitability_score'].mean():.3f}")

        print("\nSelected Stations:")
        zone_col = 'zone_id' if 'zone_id' in solution_df.columns else 'zone'
        display_cols = [
            'candidate_id',
            'name',
            zone_col,
            'fuzzy_suitability_score',
            'total_capex_inr_lakhs'
        ]
        if 'ga_rank' in solution_df.columns:
            display_cols.insert(0, 'ga_rank')
        print(solution_df[display_cols].to_string(index=False))

        # Save fitness history for plotting
        history_df = pd.DataFrame({
            'generation': range(len(fitness_history)),
            'best_fitness': fitness_history
        })
        history_path = Path("outputs/ga_results/fitness_history.csv")
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_df.to_csv(history_path, index=False)
        logger.info(f"Saved fitness history to {history_path}")

    except Exception as e:
        logger.error(f"GA optimization failed: {e}")
        raise


if __name__ == "__main__":
    import sys
    sys.exit(main())