select synthetic, seed, generator_version, generated_at
from {{ source('staging', 'dataset_metadata') }}
