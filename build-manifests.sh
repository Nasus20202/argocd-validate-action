#!/bin/bash

set -e -u -o pipefail

MANIFESTS_DIR=${1:-"manifests"}
APPS_DIR=$2
BASE_PATH=$3
SKIP_FILES=$4
HELM_KUBEVERSION=${HELM_KUBEVERSION:-$(kubectl version --client | grep "Client Version:" | cut -d' ' -f3)}

echo "Using manifests directory: $MANIFESTS_DIR"
echo "Using apps directory: $APPS_DIR"
echo "Using base path: $BASE_PATH"
echo "Skipping files matching: $SKIP_FILES"
echo "Using Helm kube version: $HELM_KUBEVERSION"

# Function to extract version from ArgoCD app manifest
get_chart_version() {
    local app_file="$1"
    yq eval '.spec.sources[] | select(.chart) | .targetRevision' "$app_file" 2>/dev/null || echo ""
}

# Function to extract chart name from ArgoCD app manifest
get_chart_name() {
    local app_file="$1"
    yq eval '.spec.sources[] | select(.chart) | .chart' "$app_file" 2>/dev/null || echo ""
}

# Function to extract repository from ArgoCD app manifest
get_chart_repo() {
    local app_file="$1"
    yq eval '.spec.sources[] | select(.chart) | .repoURL' "$app_file" 2>/dev/null || echo ""
}

# Function to extract namespace from ArgoCD app manifest
get_namespace() {
    local app_file="$1"
    yq eval '.spec.destination.namespace' "$app_file" 2>/dev/null || echo "default"
}

# Function to extract app name from ArgoCD app manifest
get_app_name() {
    local app_file="$1"
    yq eval '.metadata.name' "$app_file" 2>/dev/null || echo ""
}

# Function to extract release name from ArgoCD app manifest
get_release_name() {
    local app_file="$1"
    local release_name=$(yq eval '.spec.sources[] | select(.chart) | .helm.releaseName' "$app_file" 2>/dev/null)
    if [ -z "$release_name" ] || [ "$release_name" = "null" ]; then
        # Fallback to app name if no release name specified
        get_app_name "$app_file"
    else
        echo "$release_name"
    fi
}

# Function to extract values files from ArgoCD app manifest
get_values_files() {
    local app_file="$1"
    local values_files=""
    local current_dir=$(pwd)
    
    # Extract valueFiles from helm section
    local helm_values=$(yq eval '.spec.sources[] | select(.chart) | .helm.valueFiles[]' "$app_file" 2>/dev/null)
    
    if [ -n "$helm_values" ]; then
        while IFS= read -r values_file; do
            # Convert ArgoCD $values reference to actual file path
            if [[ "$values_file" == *"\$values/"* ]]; then
                # Remove $values/ prefix
                local_path=$(echo "$values_file" | sed "s|\$values/||")
                # If BASE_PATH is set and the path doesn't start with BASE_PATH, prepend it
                if [[ -n "$BASE_PATH" && "$local_path" != "$BASE_PATH"* ]]; then
                    local_path="$BASE_PATH/$local_path"
                fi
                if [ -f "$local_path" ]; then
                    # Convert to absolute path
                    absolute_path="$current_dir/$local_path"
                    values_files="$values_files --values $absolute_path"
                fi
            elif [ -f "$values_file" ]; then
                # Convert to absolute path
                absolute_path="$current_dir/$values_file"
                values_files="$values_files --values $absolute_path"
            fi
        done <<< "$helm_values"
    fi
    
    echo "$values_files"
}

# Function to extract helm parameters from ArgoCD app manifest
get_helm_parameters() {
    local app_file="$1"
    local parameters=""
    
    # Extract helm parameters
    local helm_params=$(yq eval '.spec.sources[] | select(.chart) | .helm.parameters[]' "$app_file" 2>/dev/null)
    
    if [ -n "$helm_params" ]; then
        while IFS= read -r param; do
            local name=$(echo "$param" | yq eval '.name' -)
            local value=$(echo "$param" | yq eval '.value' -)
            if [ -n "$name" ] && [ -n "$value" ]; then
                parameters="$parameters --set $name=$value"
            fi
        done <<< "$helm_params"
    fi
    
    echo "$parameters"
}

# Function to extract path-based sources from ArgoCD app manifest
get_path_sources() {
    local app_file="$1"
    local paths=""
    
    # Extract paths from sources (both single source and multiple sources)
    local source_paths=$(yq eval '.spec.source.path // (.spec.sources[] | select(.path) | .path)' "$app_file" 2>/dev/null)
    
    if [ -n "$source_paths" ]; then
        while IFS= read -r path; do
            if [ -n "$path" ] && [ "$path" != "null" ]; then
                local_path="$path"
                # If BASE_PATH is set and the path doesn't start with BASE_PATH, prepend it
                if [[ -n "$BASE_PATH" && "$local_path" != "$BASE_PATH"* ]]; then
                    local_path="$BASE_PATH/$local_path"
                fi
                if [ -d "$local_path" ]; then
                    paths="$paths $local_path"
                fi
            fi
        done <<< "$source_paths"
    fi
    
    echo "$paths"
}

# Function to check if an ArgoCD app is kustomize-based
is_kustomize_app() {
    local app_file="$1"
    local path_source=$(get_path_sources "$app_file")
    
    if [ -n "$path_source" ]; then
        # Check if the path contains kustomization files
        for path in $path_source; do
            if [ -f "$path/kustomization.yaml" ] || [ -f "$path/kustomization.yml" ] || [ -f "$path/kustomize.yaml" ] || [ -f "$path/kustomize.yml" ]; then
                return 0
            fi
        done
    fi
    
    return 1
}

# Function to process path-based ArgoCD applications
process_path_based_app() {
    local app_file="$1"
    local app_name=$(get_app_name "$app_file")
    local paths=$(get_path_sources "$app_file")
    
    if [ -z "$paths" ]; then
        return
    fi
    
    echo "Processing path-based application: $app_name"
    
    for path in $paths; do
        if [ ! -d "$path" ]; then
            continue
        fi
        
        local output_dir="$MANIFESTS_DIR/$app_name"
        mkdir -p "$output_dir"
        
        if is_kustomize_app "$app_file"; then
            echo "  Generating kustomize manifests from: $path"
            kubectl kustomize "$path" --output "$output_dir/manifests.yaml" || {
                echo "Error: Failed to generate kustomize manifests from $path"
                exit 1
            }
        else
            echo "  Copying manifests from: $path"
            find "$path" \( -name "*.yaml" -o -name "*.yml" \) -exec cp {} "$output_dir/" \; || {
                echo "Error: Failed to copy manifests from $path"
                exit 1
            }
        fi
    done
}

# Check if yq is available
if ! command -v yq &> /dev/null; then
    echo "yq is required but not installed. Please install it first."
    echo "Install with: sudo snap install yq"
    exit 1
fi

mkdir -p "$MANIFESTS_DIR"

# Auto-detect and process ArgoCD app files
echo "Auto-detecting ArgoCD applications..."
find "$APPS_DIR" \( -name "*.yaml" -o -name "*.yml" \) -type f | while read -r app_file; do
    if [ ! -f "$app_file" ]; then
        continue
    fi
    
    # Skip certain files
    if [[ "$(basename "$app_file")" =~ ^($SKIP_FILES)$ ]]; then
        continue
    fi
    
    app_name=$(get_app_name "$app_file")
    kind=$(yq eval '.kind' "$app_file")
    
    if [ "$kind" = "ApplicationSet" ]; then
        # Parse ApplicationSet generators
        generator_paths=$(yq eval '.spec.generators[] | select(.git) | .git.directories[].path' "$app_file" 2>/dev/null)
        name_template=$(yq eval '.spec.template.metadata.name' "$app_file" 2>/dev/null)
        
        if [ -z "$generator_paths" ]; then
            echo "Warning: No git directory generators found in ApplicationSet: $app_name"
            continue
        fi
        
        echo "Processing ApplicationSet: $app_name"
        
        # Read generator paths into array to handle them properly
        while IFS= read -r gen_path; do
            [ -z "$gen_path" ] && continue
            
            echo "  Generator path: $gen_path"
            
            # Remove /* or * from gen_path to get base_path
            gen_base=$(echo "$gen_path" | sed -e 's|/\*$||' -e 's|\*$||')
            
            # Apply BASE_PATH if set and the path doesn't start with it
            local_gen_base="$gen_base"
            if [[ -n "$BASE_PATH" && "$local_gen_base" != "$BASE_PATH"* && "$local_gen_base" != /* ]]; then
                local_gen_base="$BASE_PATH/$local_gen_base"
            fi
            
            echo "  Looking for directories in: $local_gen_base"
            
            # Check if base directory exists
            if [ ! -d "$local_gen_base" ]; then
                echo "  Warning: Base directory does not exist: $local_gen_base"
                continue
            fi
            
            # Find directories matching the pattern
            found_dirs=0
            for dir in "$local_gen_base"/*/; do
                [ -d "$dir" ] || continue
                found_dirs=1
                dirname=$(basename "$dir")
                path="$gen_base/$dirname"
                # Replace {{path.basename}} and {{path}} in name_template
                temp_app_name=$(echo "$name_template" | sed "s|{{path.basename}}|$dirname|g" | sed "s|{{path}}|$path|g")
                echo "Processing ApplicationSet generated app: $temp_app_name"
                
                # Create a temporary app manifest from the template with substituted values
                temp_app_file=$(mktemp)
                yq eval '.spec.template' "$app_file" > "$temp_app_file"
                # Substitute template variables in the temp file
                sed -i "s|{{path.basename}}|$dirname|g" "$temp_app_file"
                sed -i "s|{{path}}|$path|g" "$temp_app_file"
                
                # Check if the template has a chart-based source
                temp_chart_name=$(get_chart_name "$temp_app_file" 2>/dev/null)
                
                if [ -n "$temp_chart_name" ]; then
                    # Process as Helm chart
                    release_name=$(get_release_name "$temp_app_file")
                    chart_version=$(get_chart_version "$temp_app_file")
                    chart_repo=$(get_chart_repo "$temp_app_file")
                    namespace=$(get_namespace "$temp_app_file")
                    values_args=$(get_values_files "$temp_app_file")
                    helm_params=$(get_helm_parameters "$temp_app_file")
                    
                    echo "Generating Helm manifests for ApplicationSet app: $temp_app_name (chart: $temp_chart_name)"
                    
                    helm_cmd="helm template $release_name $temp_chart_name --version $chart_version --repo $chart_repo --namespace $namespace"
                    
                    if [ -n "$HELM_KUBEVERSION" ]; then
                        helm_cmd="$helm_cmd --kube-version $HELM_KUBEVERSION"
                    fi
                    if [ -n "$values_args" ]; then
                        helm_cmd="$helm_cmd $values_args"
                    fi
                    if [ -n "$helm_params" ]; then
                        helm_cmd="$helm_cmd $helm_params"
                    fi
                    helm_cmd="$helm_cmd --output-dir $(pwd)/$MANIFESTS_DIR"
                    
                    echo "Executing: $helm_cmd"
                    (
                        cd /tmp
                        eval "$helm_cmd"
                    ) || {
                        echo "Error: Failed to generate Helm manifests for $temp_app_name"
                        rm -f "$temp_app_file"
                        exit 1
                    }
                else
                    # Process as path-based application
                    temp_paths=$(get_path_sources "$temp_app_file")
                    output_dir="$MANIFESTS_DIR/$temp_app_name"
                    mkdir -p "$output_dir"
                    
                    if [ -n "$temp_paths" ]; then
                        for p in $temp_paths; do
                            if [ ! -d "$p" ]; then
                                continue
                            fi
                            # Check for kustomize
                            if [ -f "$p/kustomization.yaml" ] || [ -f "$p/kustomization.yml" ] || [ -f "$p/kustomize.yaml" ] || [ -f "$p/kustomize.yml" ]; then
                                echo "  Generating kustomize manifests from: $p"
                                kubectl kustomize "$p" --output "$output_dir/manifests.yaml" || {
                                    echo "Error: Failed to generate kustomize manifests from $p"
                                    rm -f "$temp_app_file"
                                    exit 1
                                }
                            else
                                echo "  Copying manifests from: $p"
                                find "$p" \( -name "*.yaml" -o -name "*.yml" \) -exec cp {} "$output_dir/" \;
                            fi
                        done
                    else
                        # Fallback: copy manifests from the generated path directly
                        local_path="$path"
                        if [[ -n "$BASE_PATH" && "$local_path" != "$BASE_PATH"* && "$local_path" != /* ]]; then
                            local_path="$BASE_PATH/$local_path"
                        fi
                        if [ -d "$local_path" ]; then
                            # Check for kustomize
                            if [ -f "$local_path/kustomization.yaml" ] || [ -f "$local_path/kustomization.yml" ] || [ -f "$local_path/kustomize.yaml" ] || [ -f "$local_path/kustomize.yml" ]; then
                                echo "  Generating kustomize manifests from: $local_path"
                                kubectl kustomize "$local_path" --output "$output_dir/manifests.yaml" || {
                                    echo "Error: Failed to generate kustomize manifests from $local_path"
                                    rm -f "$temp_app_file"
                                    exit 1
                                }
                            else
                                echo "  Copying manifests from: $local_path"
                                find "$local_path" \( -name "*.yaml" -o -name "*.yml" \) -exec cp {} "$output_dir/" \;
                            fi
                        fi
                    fi
                fi
                
                rm -f "$temp_app_file"
            done
            
            if [ "$found_dirs" -eq 0 ]; then
                echo "  Warning: No subdirectories found in $local_gen_base"
            fi
        done <<< "$generator_paths"
    else
        chart_name=$(get_chart_name "$app_file")
        
        # Check if this is a chart-based application
        if [ -n "$chart_name" ]; then
            # Process as Helm chart
            release_name=$(get_release_name "$app_file")
            chart_version=$(get_chart_version "$app_file")
            chart_repo=$(get_chart_repo "$app_file")
            namespace=$(get_namespace "$app_file")
            
            # Skip if no chart information
            if [ -z "$chart_version" ] || [ -z "$chart_repo" ]; then
                echo "Error: Missing chart information for $app_file"
                echo "Chart: $chart_name, Version: $chart_version, Repo: $chart_repo"
                exit 1
            fi
            
            # Extract values files from ArgoCD app manifest
            values_args=$(get_values_files "$app_file")
            
            # Extract helm parameters from ArgoCD app manifest
            helm_params=$(get_helm_parameters "$app_file")
            
            echo "Generating manifests for $app_name (chart: $chart_name, version: $chart_version, release: $release_name)..."
            
            # Build helm template command using --repo
            helm_cmd="helm template $release_name $chart_name --version $chart_version --repo $chart_repo --namespace $namespace"
            
            if [ -n "$HELM_KUBEVERSION" ]; then
                helm_cmd="$helm_cmd --kube-version $HELM_KUBEVERSION"
            fi
            
            if [ -n "$values_args" ]; then
                helm_cmd="$helm_cmd $values_args"
            fi
            
            if [ -n "$helm_params" ]; then
                helm_cmd="$helm_cmd $helm_params"
            fi
            
            helm_cmd="$helm_cmd --output-dir $(pwd)/$MANIFESTS_DIR"
            
            # Execute the command in a temporary directory to avoid conflicts
            echo "Executing: $helm_cmd"
            (
                cd /tmp
                eval "$helm_cmd"
            ) || {
                echo "Error: Failed to generate manifests for $app_name"
                echo "Command: $helm_cmd"
                exit 1
            }
            
            # Process any additional path-based sources
            process_path_based_app "$app_file"
        else
            # Process as path-based application
            process_path_based_app "$app_file"
        fi
    fi
done

echo "All manifests generated successfully!"
echo "Generated manifests are available in: $MANIFESTS_DIR"
