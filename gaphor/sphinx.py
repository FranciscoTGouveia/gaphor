from __future__ import annotations

import functools
from pathlib import Path

import sphinx.util.docutils
from docutils import nodes
from docutils.parsers.rst import directives
from docutils.parsers.rst.directives import images
from sphinx.util import logging

from gaphor.core.eventmanager import EventManager
from gaphor.core.modeling import Diagram, ElementFactory
from gaphor.i18n import gettext
from gaphor.plugins.diagramexport import DiagramExport
from gaphor.services.modelinglanguage import ModelingLanguageService
from gaphor.storage import storage

log = logging.getLogger(__name__)


def setup(app: sphinx.application.Sphinx) -> dict[str, object]:
    """Called by Sphinx to set up the extension."""
    app.add_config_value("gaphor_models", {}, "env", [dict])

    app.add_directive("diagram", DiagramDirective)

    app.connect("config-inited", config_inited)

    return {
        "version": "0.1",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }


def config_inited(app, config):
    log.info(f"Gaphor models: {config.gaphor_models}")
    if isinstance(config.gaphor_models, str):
        config.gaphor_models = {"default": config.gaphor_models}


class DiagramDirective(sphinx.util.docutils.SphinxDirective):
    """The Gaphor diagram directive.

    Usage: "``.. diagram:: diagram-name``".
    """

    has_content = False
    required_arguments = 1
    final_argument_whitespace = True
    option_spec = {
        "model": directives.unchanged,
        **images.Image.option_spec,
    }

    def run(self) -> list[nodes.Node]:
        name = self.arguments[0]
        model_name = self.options.get("model", "default")
        model_file = self.config.gaphor_models.get(model_name)

        if not model_file:
            return self.logging_error_node(
                gettext("No model file configured for model '{model_name}'.").format(
                    model_name=model_name
                )
            )

        rel_filename, filename = self.env.relfn2path(model_file)
        self.env.note_dependency(rel_filename)
        model = load_model(filename)

        outdir = (
            Path(self.env.app.doctreedir).relative_to(self.env.srcdir) / ".." / "gaphor"
        )
        outdir.mkdir(exist_ok=True)

        diagram = next(
            model.select(
                lambda e: isinstance(e, Diagram) and ".".join(e.qualifiedName) == name
            ),
            None,
        )

        if not diagram:
            diagram = next(
                model.select(lambda e: isinstance(e, Diagram) and e.name == name), None
            )

        if not diagram:
            return self.logging_error_node(
                gettext(
                    "No diagram '{name}' in model '{model_name}' ({model_file})."
                ).format(name=name, model_name=model_name, model_file=model_file)
            )

        outfile = outdir / f"{diagram.id}"
        DiagramExport().save_svg(outfile.with_suffix(".svg"), diagram)
        DiagramExport().save_pdf(outfile.with_suffix(".pdf"), diagram)

        return [
            nodes.image(
                rawsource=self.block_text,
                uri=str(outfile) + ".*",
                **self.options,
            ),
        ]

    def logging_error_node(self, text: str) -> list[nodes.Node]:
        location = self.state_machine.get_source_and_line(self.lineno)
        log.error(text, location=location)
        return [nodes.error("", nodes.paragraph(text=text))]


@functools.lru_cache(maxsize=None)
def load_model(model_file: str) -> ElementFactory:
    element_factory = ElementFactory()
    storage.load(
        model_file,
        element_factory,
        modeling_language=ModelingLanguageService(EventManager()),
    )
    return element_factory