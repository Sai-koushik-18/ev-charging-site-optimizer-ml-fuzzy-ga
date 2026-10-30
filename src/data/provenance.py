"""
Data provenance tracking system.
Tracks the origin (REAL, DERIVED, SYNTHETIC) of every column in every dataset.
"""

from enum import Enum
from typing import Dict, List, Optional, Any
import json
import pandas as pd
from dataclasses import dataclass, field, asdict
from datetime import datetime


class Provenance(str, Enum):
    """Data provenance classification."""
    REAL = "REAL"           # Directly from authoritative source
    DERIVED = "DERIVED"     # Computed from REAL data
    SYNTHETIC = "SYNTHETIC" # Generated from statistical models
    META = "META"           # Metadata columns (provenance tracking itself)

    @classmethod
    def validate(cls, value: str) -> "Provenance":
        """Validate and return Provenance enum."""
        try:
            return cls(value.upper())
        except ValueError:
            raise ValueError(
                f"Invalid provenance value: '{value}'. "
                f"Must be one of: {[p.value for p in cls]}"
            )


@dataclass
class ColumnProvenance:
    """Provenance metadata for a single column."""
    column: str
    provenance: Provenance
    source: str  # Specific source (e.g., "openstreetmap", "synthetic_demand_model")
    description: str
    transformation: Optional[str] = None  # How derived/synthetic data was created
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ColumnProvenance":
        data = data.copy()
        data["provenance"] = Provenance(data["provenance"])
        return cls(**data)


@dataclass
class DatasetProvenance:
    """Complete provenance record for a dataset."""
    dataset_name: str
    generated_at: str
    generated_by: str  # Script/module name
    columns: Dict[str, ColumnProvenance] = field(default_factory=dict)
    row_count: int = 0
    source_files: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def add_column(self, col_prov: ColumnProvenance) -> None:
        self.columns[col_prov.column] = col_prov

    def get_provenance_map(self) -> Dict[str, str]:
        """Return simple column -> provenance string mapping."""
        return {col: cp.provenance.value for col, cp in self.columns.items()}

    def to_json_string(self) -> str:
        """Serialize to JSON string for storage in DataFrame."""
        data = {
            "dataset_name": self.dataset_name,
            "generated_at": self.generated_at,
            "generated_by": self.generated_by,
            "columns": {k: v.to_dict() for k, v in self.columns.items()},
            "row_count": self.row_count,
            "source_files": self.source_files,
            "parameters": self.parameters,
            "notes": self.notes,
        }
        return json.dumps(data, default=str)

    @classmethod
    def from_json_string(cls, json_str: str) -> "DatasetProvenance":
        data = json.loads(json_str)
        columns = {k: ColumnProvenance.from_dict(v) for k, v in data["columns"].items()}
        return cls(
            dataset_name=data["dataset_name"],
            generated_at=data["generated_at"],
            generated_by=data["generated_by"],
            columns=columns,
            row_count=data.get("row_count", 0),
            source_files=data.get("source_files", []),
            parameters=data.get("parameters", {}),
            notes=data.get("notes", ""),
        )

    def summary(self) -> Dict[str, int]:
        """Count columns by provenance type."""
        counts = {p.value: 0 for p in Provenance}
        for cp in self.columns.values():
            counts[cp.provenance.value] += 1
        return counts


class ProvenanceTracker:
    """
    Tracks provenance during data pipeline execution.
    Attaches provenance metadata to DataFrames.
    """

    PROVENANCE_COLUMN = "data_provenance"
    PROVENANCE_ATTR = "_dataset_provenance"

    def __init__(self, dataset_name: str, generated_by: str):
        self.dataset_provenance = DatasetProvenance(
            dataset_name=dataset_name,
            generated_at=datetime.now().isoformat(),
            generated_by=generated_by,
        )

    def add_column(
        self,
        column: str,
        provenance: Provenance,
        source: str,
        description: str,
        transformation: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> "ProvenanceTracker":
        """Register provenance for a column."""
        col_prov = ColumnProvenance(
            column=column,
            provenance=provenance,
            source=source,
            description=description,
            transformation=transformation,
            parameters=parameters or {},
        )
        self.dataset_provenance.add_column(col_prov)
        return self

    def add_columns_batch(
        self,
        columns: List[str],
        provenance: Provenance,
        source: str,
        description: str,
        transformation: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> "ProvenanceTracker":
        """Register same provenance for multiple columns."""
        for col in columns:
            self.add_column(col, provenance, source, description, transformation, parameters)
        return self

    def finalize(self, df: pd.DataFrame, source_files: Optional[List[str]] = None, notes: str = "") -> pd.DataFrame:
        """
        Attach provenance to DataFrame and return it.

        Adds a 'data_provenance' column with JSON string of full provenance.
        Also sets _dataset_provenance attribute on DataFrame.
        """
        self.dataset_provenance.row_count = len(df)
        if source_files:
            self.dataset_provenance.source_files = source_files
        self.dataset_provenance.notes = notes

        # Attach as attribute (not serialized to CSV)
        df.attrs[self.PROVENANCE_ATTR] = self.dataset_provenance

        # Add provenance column for CSV/Parquet persistence
        prov_json = self.dataset_provenance.to_json_string()
        df[self.PROVENANCE_COLUMN] = prov_json

        return df

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> Optional["DatasetProvenance"]:
        """Extract provenance from DataFrame."""
        # Try attribute first (in-memory)
        if hasattr(df, "attrs") and cls.PROVENANCE_ATTR in df.attrs:
            return df.attrs[cls.PROVENANCE_ATTR]

        # Try column (persisted)
        if cls.PROVENANCE_COLUMN in df.columns:
            prov_json = df[cls.PROVENANCE_COLUMN].iloc[0]
            return DatasetProvenance.from_json_string(prov_json)

        return None

    @classmethod
    def get_column_provenance(cls, df: pd.DataFrame, column: str) -> Optional[Provenance]:
        """Get provenance of a specific column."""
        prov = cls.from_dataframe(df)
        if prov and column in prov.columns:
            return prov.columns[column].provenance
        return None

    @classmethod
    def validate_provenance_complete(cls, df: pd.DataFrame, required_columns: List[str]) -> List[str]:
        """Check if all required columns have provenance tracking."""
        prov = cls.from_dataframe(df)
        if not prov:
            return [f"No provenance data found for dataset"]

        missing = []
        for col in required_columns:
            if col not in prov.columns:
                missing.append(f"Column '{col}' missing from provenance tracking")
        return missing


def create_provenance_column(provenance_map: Dict[str, Provenance]) -> pd.Series:
    """Create a provenance column for a DataFrame (alternative to full tracker)."""
    return pd.Series([json.dumps({k: v.value for k, v in provenance_map.items()})] * len(provenance_map))