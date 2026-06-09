from e2b import Template, default_build_logger

if __name__ == "__main__":
    template = (
        Template()
        .skip_cache()
        .from_dockerfile("Dockerfile")
        
    )

    Template.build(
        template,
        alias=f"perf-bench",
        cpu_count=2,
        memory_mb=2048,
        on_build_logs=default_build_logger(),
    )

